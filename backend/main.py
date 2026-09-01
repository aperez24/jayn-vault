from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

app = FastAPI(title="JAYN Vault API", version="0.4.0")

CONFIG_PATH = Path(os.getenv("JAYN_VAULT_CONFIG", "/var/lib/jayn-vault/config.json"))
HISTORY_PATH = Path(os.getenv("JAYN_VAULT_HISTORY", "/var/lib/jayn-vault/history.json"))
DEFAULT_STORAGE_ROOTS = [
    {"id": "jaynos", "name": "jaynOS", "path": "/mnt/jayn-vault/sources/jaynos"},
]
DEFAULT_SCHEDULE = {
    "timezone": "America/New_York",
    "daily": {"enabled": True, "time": "06:00"},
    "weekly": {"enabled": True, "day": "sunday", "time": "02:00"},
}
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
SNAPSHOT_SUFFIXES = ("_manual", "_daily", "_weekly")
REPOSITORY_META_DIR = ".jayn-vault"

_JOB_LOCK = threading.Lock()
_JOB_STATE: dict = {
    "id": None,
    "status": "idle",
    "phase": "idle",
    "percent": 0.0,
    "trigger": None,
    "source": None,
    "destination": None,
    "snapshot": None,
    "previous_snapshot": None,
    "hardlink_supported": None,
    "total_files": 0,
    "processed_files": 0,
    "copied_files": 0,
    "linked_files": 0,
    "skipped_files": 0,
    "failed_files": 0,
    "total_bytes": 0,
    "required_bytes": 0,
    "processed_bytes": 0,
    "copied_bytes": 0,
    "current_file": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
}


class SelectionRequest(BaseModel):
    kind: Literal["source", "destination"]
    path: str


class SelectionState(BaseModel):
    source: str | None = None
    destination: str | None = None


class DailySchedule(BaseModel):
    enabled: bool = True
    time: str = "06:00"

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _parse_hhmm(value)
        return value


class WeeklySchedule(BaseModel):
    enabled: bool = True
    day: Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"] = "sunday"
    time: str = "02:00"

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        _parse_hhmm(value)
        return value


class ScheduleRequest(BaseModel):
    timezone: str = "America/New_York"
    daily: DailySchedule = Field(default_factory=DailySchedule)
    weekly: WeeklySchedule = Field(default_factory=WeeklySchedule)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("Time must use 24-hour HH:MM format") from exc
    return parsed.hour, parsed.minute


def _default_config() -> dict:
    return {
        "source": None,
        "destination": None,
        "schedule": json.loads(json.dumps(DEFAULT_SCHEDULE)),
    }


def _load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _default_config()
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Unable to read Vault configuration: {exc}") from exc

    if not isinstance(data, dict):
        data = _default_config()
    data.setdefault("source", None)
    data.setdefault("destination", None)
    data.setdefault("schedule", json.loads(json.dumps(DEFAULT_SCHEDULE)))
    schedule = data["schedule"]
    if not isinstance(schedule, dict):
        schedule = json.loads(json.dumps(DEFAULT_SCHEDULE))
        data["schedule"] = schedule
    schedule.setdefault("timezone", DEFAULT_SCHEDULE["timezone"])
    schedule.setdefault("daily", dict(DEFAULT_SCHEDULE["daily"]))
    schedule.setdefault("weekly", dict(DEFAULT_SCHEDULE["weekly"]))
    return data


def _save_config(data: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = CONFIG_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temp, CONFIG_PATH)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to save Vault configuration: {exc}") from exc


def _resolve_dir(raw_path: str) -> Path:
    if not raw_path:
        raise HTTPException(status_code=400, detail="A path is required.")

    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise HTTPException(status_code=400, detail="Filesystem paths must be absolute.")

    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=404, detail=f"Path is not available: {raw_path}") from exc

    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="The selected path is not a directory.")

    return resolved


def _storage_roots() -> list[dict]:
    raw = os.getenv("JAYN_VAULT_STORAGE_ROOTS")
    roots = DEFAULT_STORAGE_ROOTS
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                roots = parsed
        except json.JSONDecodeError:
            pass

    result = []
    for root in roots:
        try:
            path = _resolve_dir(str(root.get("path", "")))
        except HTTPException:
            continue
        result.append(
            {
                "id": str(root.get("id") or path.name),
                "name": str(root.get("name") or path.name),
                "path": str(path),
                "readable": os.access(path, os.R_OK | os.X_OK),
                "writable": os.access(path, os.W_OK | os.X_OK),
            }
        )
    return result


def _scan_source(source: Path) -> tuple[list[dict], list[str], int]:
    files: list[dict] = []
    directories: list[str] = []
    total_bytes = 0

    for root, dirs, names in os.walk(source, followlinks=False):
        root_path = Path(root)
        relative_root = root_path.relative_to(source)

        usable_dirs: list[str] = []
        for dirname in dirs:
            directory = root_path / dirname
            if directory.is_symlink():
                continue
            usable_dirs.append(dirname)
            directories.append(str((relative_root / dirname).as_posix()))
        dirs[:] = usable_dirs

        for name in names:
            path = root_path / name
            try:
                if path.is_symlink():
                    continue
                stat = path.stat()
            except (PermissionError, FileNotFoundError, OSError):
                continue
            relative = str(path.relative_to(source).as_posix())
            files.append(
                {
                    "path": path,
                    "relative": relative,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                }
            )
            total_bytes += stat.st_size

    return files, directories, total_bytes


def _directory_size(directory: Path) -> tuple[int, int, int]:
    files, directories, total_bytes = _scan_source(directory)
    return total_bytes, len(files), len(directories)


def _next_daily(schedule: dict, timezone: ZoneInfo, now: datetime) -> datetime | None:
    if not schedule.get("enabled", True):
        return None
    hour, minute = _parse_hhmm(str(schedule.get("time", "06:00")))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _next_weekly(schedule: dict, timezone: ZoneInfo, now: datetime) -> datetime | None:
    if not schedule.get("enabled", True):
        return None
    day = str(schedule.get("day", "sunday")).lower()
    if day not in WEEKDAYS:
        day = "sunday"
    hour, minute = _parse_hhmm(str(schedule.get("time", "02:00")))
    days_ahead = (WEEKDAYS[day] - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _schedule_payload(data: dict) -> dict:
    schedule = data.get("schedule") or DEFAULT_SCHEDULE
    timezone_name = str(schedule.get("timezone") or DEFAULT_SCHEDULE["timezone"])
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = DEFAULT_SCHEDULE["timezone"]
        timezone = ZoneInfo(timezone_name)

    now = datetime.now(timezone)
    daily = {**DEFAULT_SCHEDULE["daily"], **(schedule.get("daily") or {})}
    weekly = {**DEFAULT_SCHEDULE["weekly"], **(schedule.get("weekly") or {})}
    next_daily = _next_daily(daily, timezone, now)
    next_weekly = _next_weekly(weekly, timezone, now)

    return {
        "timezone": timezone_name,
        "daily": {**daily, "next_run": next_daily.isoformat() if next_daily else None},
        "weekly": {**weekly, "next_run": next_weekly.isoformat() if next_weekly else None},
        "server_now": now.isoformat(),
    }


def _snapshot_job() -> dict:
    with _JOB_LOCK:
        return dict(_JOB_STATE)


def _set_job(**updates) -> dict:
    with _JOB_LOCK:
        _JOB_STATE.update(updates)
        total = int(_JOB_STATE.get("total_bytes") or 0)
        processed = int(_JOB_STATE.get("processed_bytes") or 0)
        if total > 0:
            _JOB_STATE["percent"] = round(min(100.0, (processed / total) * 100.0), 1)
        elif _JOB_STATE.get("status") == "completed":
            _JOB_STATE["percent"] = 100.0
        return dict(_JOB_STATE)


def _append_history(job: dict) -> None:
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                history = []
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            history = []
        history.insert(0, job)
        history = history[:100]
        temp = HISTORY_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(history, indent=2), encoding="utf-8")
        os.replace(temp, HISTORY_PATH)
    except OSError:
        pass


def _path_contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_backup_paths(source: Path, destination: Path) -> None:
    if source == destination:
        raise HTTPException(status_code=400, detail="Source and destination cannot be the same folder.")
    if _path_contains(source, destination):
        raise HTTPException(status_code=400, detail="Destination cannot be inside the source folder.")
    if _path_contains(destination, source):
        raise HTTPException(status_code=400, detail="Source cannot be inside the destination folder.")


def _repository_meta_path(repository: Path) -> Path:
    return repository / REPOSITORY_META_DIR / "repository.json"


def _load_repository_meta(repository: Path) -> dict:
    path = _repository_meta_path(repository)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_repository_meta(repository: Path, data: dict) -> None:
    meta_dir = repository / REPOSITORY_META_DIR
    meta_dir.mkdir(parents=True, exist_ok=True)
    path = meta_dir / "repository.json"
    temp = meta_dir / ".repository.json.tmp"
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _completed_snapshots(repository: Path) -> list[Path]:
    snapshots: list[Path] = []
    try:
        for entry in repository.iterdir():
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if not any(entry.name.endswith(suffix) or f"{suffix}_" in entry.name for suffix in SNAPSHOT_SUFFIXES):
                continue
            manifest = entry / REPOSITORY_META_DIR / "manifest.json"
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("status") == "completed":
                    snapshots.append(entry)
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                continue
    except OSError:
        return []
    snapshots.sort(key=lambda path: path.name)
    return snapshots


def _latest_snapshot(repository: Path) -> Path | None:
    snapshots = _completed_snapshots(repository)
    return snapshots[-1] if snapshots else None


def _hardlink_capability(repository: Path) -> bool:
    test_id = uuid.uuid4().hex[:10]
    source = repository / f".jayn-vault-link-source-{test_id}"
    linked = repository / f".jayn-vault-link-target-{test_id}"
    try:
        source.write_bytes(b"JAYN")
        os.link(source, linked)
        # CIFS mounts using noserverino can present different client-side inode
        # numbers for two names that still represent a valid server-side hard link.
        # A successful link() operation is therefore the authoritative capability test.
        return linked.exists()
    except OSError:
        return False
    finally:
        for path in (linked, source):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass


def _file_matches_previous(file_info: dict, previous_snapshot: Path | None) -> Path | None:
    if previous_snapshot is None:
        return None
    previous_file = previous_snapshot / file_info["relative"]
    try:
        stat = previous_file.stat()
        if previous_file.is_file() and stat.st_size == file_info["size"] and stat.st_mtime_ns == file_info["mtime_ns"]:
            return previous_file
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return None


def _plan_required_bytes(files: list[dict], previous_snapshot: Path | None, hardlink_supported: bool) -> tuple[int, int]:
    if previous_snapshot is None or not hardlink_supported:
        return sum(int(item["size"]) for item in files), 0

    required = 0
    reusable = 0
    for item in files:
        if _file_matches_previous(item, previous_snapshot) is not None:
            reusable += 1
        else:
            required += int(item["size"])
    return required, reusable


def _snapshot_name(started: datetime, trigger: str, repository: Path) -> str:
    base = f"{started.strftime('%Y-%m-%d_%H-%M-%S')}_{trigger}"
    candidate = base
    counter = 1
    while (repository / candidate).exists() or (repository / f".{candidate}.inprogress").exists():
        candidate = f"{base}_{counter:02d}"
        counter += 1
    return candidate


def _copy_file_with_progress(source_file: Path, destination_file: Path) -> int:
    temp_file = destination_file.with_name(f".{destination_file.name}.jayn-vault-part")
    copied = 0
    destination_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source_file.open("rb") as src, temp_file.open("wb") as dst:
            while True:
                chunk = src.read(4 * 1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
                copied += len(chunk)
                state = _snapshot_job()
                _set_job(
                    processed_bytes=int(state.get("processed_bytes") or 0) + len(chunk),
                    copied_bytes=int(state.get("copied_bytes") or 0) + len(chunk),
                )
        shutil.copystat(source_file, temp_file, follow_symlinks=False)
        os.replace(temp_file, destination_file)
        return copied
    finally:
        try:
            if temp_file.exists():
                temp_file.unlink()
        except OSError:
            pass


def _write_manifest(snapshot_root: Path, payload: dict) -> None:
    meta_dir = snapshot_root / REPOSITORY_META_DIR
    meta_dir.mkdir(parents=True, exist_ok=True)
    path = meta_dir / "manifest.json"
    temp = meta_dir / ".manifest.json.tmp"
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _run_backup_worker(job_id: str, source: Path, repository: Path, trigger: str = "manual") -> None:
    started = datetime.now().astimezone()
    inprogress: Path | None = None
    try:
        _set_job(
            id=job_id,
            status="running",
            phase="scanning",
            percent=0.0,
            trigger=trigger,
            source=str(source),
            destination=str(repository),
            snapshot=None,
            previous_snapshot=None,
            hardlink_supported=None,
            total_files=0,
            processed_files=0,
            copied_files=0,
            linked_files=0,
            skipped_files=0,
            failed_files=0,
            total_bytes=0,
            required_bytes=0,
            processed_bytes=0,
            copied_bytes=0,
            current_file=None,
            started_at=started.isoformat(),
            finished_at=None,
            error=None,
        )

        files, directories, total_bytes = _scan_source(source)
        previous_snapshot = _latest_snapshot(repository)
        hardlink_supported = _hardlink_capability(repository)
        required_bytes, reusable_files = _plan_required_bytes(files, previous_snapshot, hardlink_supported)
        usage = shutil.disk_usage(repository)
        if usage.free < required_bytes:
            raise RuntimeError(f"Destination does not have enough free space. Short by {required_bytes - usage.free} bytes.")

        snapshot_name = _snapshot_name(started, trigger, repository)
        final_snapshot = repository / snapshot_name
        inprogress = repository / f".{snapshot_name}.inprogress"
        inprogress.mkdir(parents=False, exist_ok=False)

        for relative in directories:
            (inprogress / relative).mkdir(parents=True, exist_ok=True)

        _set_job(
            phase="copying",
            snapshot=snapshot_name,
            previous_snapshot=previous_snapshot.name if previous_snapshot else None,
            hardlink_supported=hardlink_supported,
            total_files=len(files),
            total_bytes=total_bytes,
            required_bytes=required_bytes,
        )

        for item in files:
            source_file = Path(item["path"])
            relative = str(item["relative"])
            file_size = int(item["size"])
            target = inprogress / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _set_job(current_file=relative)

            try:
                previous_file = _file_matches_previous(item, previous_snapshot) if hardlink_supported else None
                if previous_file is not None:
                    try:
                        os.link(previous_file, target)
                        state = _snapshot_job()
                        _set_job(
                            processed_bytes=int(state.get("processed_bytes") or 0) + file_size,
                            processed_files=int(state.get("processed_files") or 0) + 1,
                            linked_files=int(state.get("linked_files") or 0) + 1,
                            skipped_files=int(state.get("skipped_files") or 0) + 1,
                        )
                        continue
                    except OSError:
                        if shutil.disk_usage(repository).free < file_size:
                            raise RuntimeError(f"Not enough free space to fall back to copying {relative}.")

                _copy_file_with_progress(source_file, target)
                state = _snapshot_job()
                _set_job(
                    processed_files=int(state.get("processed_files") or 0) + 1,
                    copied_files=int(state.get("copied_files") or 0) + 1,
                )
            except Exception:
                state = _snapshot_job()
                _set_job(
                    processed_files=int(state.get("processed_files") or 0) + 1,
                    failed_files=int(state.get("failed_files") or 0) + 1,
                )
                raise

        finished = datetime.now().astimezone()
        state = _snapshot_job()
        manifest = {
            "format_version": 1,
            "status": "completed",
            "job_id": job_id,
            "trigger": trigger,
            "snapshot": snapshot_name,
            "previous_snapshot": previous_snapshot.name if previous_snapshot else None,
            "hardlink_supported": hardlink_supported,
            "source": str(source),
            "repository": str(repository),
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "total_files": len(files),
            "logical_bytes": total_bytes,
            "physical_bytes_written": int(state.get("copied_bytes") or 0),
            "required_bytes_estimate": required_bytes,
            "copied_files": int(state.get("copied_files") or 0),
            "reused_files": int(state.get("linked_files") or 0),
            "failed_files": int(state.get("failed_files") or 0),
        }
        _write_manifest(inprogress, manifest)
        os.replace(inprogress, final_snapshot)
        inprogress = None

        snapshots = _completed_snapshots(repository)
        _save_repository_meta(
            repository,
            {
                "format_version": 1,
                "mode": "hardlink-snapshots" if hardlink_supported else "full-snapshots",
                "hardlink_supported": hardlink_supported,
                "latest_snapshot": snapshot_name,
                "snapshot_count": len(snapshots),
                "updated_at": finished.isoformat(),
            },
        )

        final = _set_job(
            status="completed",
            phase="complete",
            percent=100.0,
            current_file=None,
            snapshot=snapshot_name,
            finished_at=finished.isoformat(),
        )
        _append_history(final)
    except Exception as exc:
        if inprogress is not None:
            try:
                shutil.rmtree(inprogress)
            except OSError:
                pass
        finished = datetime.now().astimezone()
        final = _set_job(
            status="failed",
            phase="failed",
            current_file=None,
            finished_at=finished.isoformat(),
            error=str(exc),
        )
        _append_history(final)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "jayn-vault-api"}


@app.get("/api/fs/roots")
def filesystem_roots() -> dict:
    return {"roots": _storage_roots()}


@app.get("/api/fs/list")
def list_directory(path: str = Query(default="/")) -> dict:
    directory = _resolve_dir(path)

    if not os.access(directory, os.R_OK | os.X_OK):
        raise HTTPException(status_code=403, detail="The JAYN Vault service account cannot browse this directory.")

    items: list[dict] = []
    try:
        for entry in os.scandir(directory):
            try:
                is_dir = entry.is_dir(follow_symlinks=True)
                is_file = entry.is_file(follow_symlinks=True)
                stat = entry.stat(follow_symlinks=True)
                items.append(
                    {
                        "name": entry.name,
                        "path": str(Path(entry.path).resolve(strict=False)),
                        "type": "directory" if is_dir else "file" if is_file else "other",
                        "size": stat.st_size if is_file else None,
                        "readable": os.access(entry.path, os.R_OK),
                        "writable": os.access(entry.path, os.W_OK),
                    }
                )
            except (PermissionError, FileNotFoundError, OSError):
                continue
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Permission denied while browsing this directory.") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to browse directory: {exc}") from exc

    items.sort(key=lambda item: (item["type"] != "directory", item["name"].casefold()))
    parent = None if directory == directory.parent else str(directory.parent)
    return {
        "path": str(directory),
        "parent": parent,
        "readable": os.access(directory, os.R_OK),
        "writable": os.access(directory, os.W_OK),
        "items": items,
    }


@app.get("/api/config/selection", response_model=SelectionState)
def get_selection() -> dict:
    data = _load_config()
    return {"source": data.get("source"), "destination": data.get("destination")}


@app.post("/api/config/selection", response_model=SelectionState)
def set_selection(request: SelectionRequest) -> dict:
    directory = _resolve_dir(request.path)
    if request.kind == "source":
        if not os.access(directory, os.R_OK | os.X_OK):
            raise HTTPException(status_code=403, detail="The JAYN Vault service account cannot read this source directory.")
    else:
        if not os.access(directory, os.W_OK | os.X_OK):
            raise HTTPException(status_code=403, detail="The JAYN Vault service account cannot write to this destination directory.")

    data = _load_config()
    data[request.kind] = str(directory)
    _save_config(data)
    return {"source": data.get("source"), "destination": data.get("destination")}


@app.get("/api/config/schedule")
def get_schedule() -> dict:
    return _schedule_payload(_load_config())


@app.post("/api/config/schedule")
def set_schedule(request: ScheduleRequest) -> dict:
    data = _load_config()
    data["schedule"] = request.model_dump()
    _save_config(data)
    return _schedule_payload(data)


@app.get("/api/storage/status")
def storage_status() -> dict:
    data = _load_config()
    source_raw = data.get("source")
    destination_raw = data.get("destination")

    source = None
    destination = None
    source_files: list[dict] = []

    if source_raw:
        source_path = _resolve_dir(source_raw)
        if not os.access(source_path, os.R_OK | os.X_OK):
            raise HTTPException(status_code=403, detail="The configured source is not readable.")
        source_files, directories, total_bytes = _scan_source(source_path)
        source = {
            "path": str(source_path),
            "bytes": total_bytes,
            "files": len(source_files),
            "directories": len(directories),
        }

    required_bytes = source["bytes"] if source is not None else 0
    reusable_files = 0

    if destination_raw:
        destination_path = _resolve_dir(destination_raw)
        if not os.access(destination_path, os.W_OK | os.X_OK):
            raise HTTPException(status_code=403, detail="The configured destination is not writable.")
        usage = shutil.disk_usage(destination_path)
        meta = _load_repository_meta(destination_path)
        previous = _latest_snapshot(destination_path)
        hardlink_supported = bool(meta.get("hardlink_supported")) if previous is not None else None
        if source is not None and previous is not None and hardlink_supported:
            required_bytes, reusable_files = _plan_required_bytes(source_files, previous, True)
        snapshots = _completed_snapshots(destination_path)
        destination = {
            "path": str(destination_path),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "required_bytes": required_bytes,
            "reusable_files": reusable_files,
            "snapshot_count": len(snapshots),
            "latest_snapshot": snapshots[-1].name if snapshots else None,
            "hardlink_supported": hardlink_supported,
        }

    capacity_ok = None
    shortfall_bytes = 0
    if source is not None and destination is not None:
        capacity_ok = destination["free_bytes"] >= required_bytes
        if not capacity_ok:
            shortfall_bytes = required_bytes - destination["free_bytes"]

    return {
        "source": source,
        "destination": destination,
        "capacity_ok": capacity_ok,
        "shortfall_bytes": shortfall_bytes,
        "required_bytes": required_bytes,
        "reusable_files": reusable_files,
    }


@app.get("/api/repository/status")
def repository_status() -> dict:
    data = _load_config()
    destination_raw = data.get("destination")
    if not destination_raw:
        return {"configured": False, "snapshot_count": 0, "latest_snapshot": None}
    repository = _resolve_dir(destination_raw)
    snapshots = _completed_snapshots(repository)
    meta = _load_repository_meta(repository)
    return {
        "configured": True,
        "path": str(repository),
        "snapshot_count": len(snapshots),
        "latest_snapshot": snapshots[-1].name if snapshots else None,
        "hardlink_supported": meta.get("hardlink_supported"),
        "mode": meta.get("mode"),
        "snapshots": [path.name for path in reversed(snapshots[-20:])],
    }


@app.get("/api/jobs/current")
def current_job() -> dict:
    return _snapshot_job()


@app.post("/api/jobs/run")
def run_backup_now() -> dict:
    current = _snapshot_job()
    if current.get("status") == "running":
        raise HTTPException(status_code=409, detail="A backup is already running.")

    data = _load_config()
    source_raw = data.get("source")
    destination_raw = data.get("destination")
    if not source_raw or not destination_raw:
        raise HTTPException(status_code=400, detail="Both a source and destination must be selected before running a backup.")

    source = _resolve_dir(source_raw)
    repository = _resolve_dir(destination_raw)
    _validate_backup_paths(source, repository)

    if not os.access(source, os.R_OK | os.X_OK):
        raise HTTPException(status_code=403, detail="The configured source is not readable.")
    if not os.access(repository, os.W_OK | os.X_OK):
        raise HTTPException(status_code=403, detail="The configured destination is not writable.")

    job_id = uuid.uuid4().hex[:12]
    _set_job(
        id=job_id,
        status="running",
        phase="starting",
        percent=0.0,
        trigger="manual",
        source=str(source),
        destination=str(repository),
        snapshot=None,
        previous_snapshot=None,
        hardlink_supported=None,
        total_files=0,
        processed_files=0,
        copied_files=0,
        linked_files=0,
        skipped_files=0,
        failed_files=0,
        total_bytes=0,
        required_bytes=0,
        processed_bytes=0,
        copied_bytes=0,
        current_file=None,
        started_at=datetime.now().astimezone().isoformat(),
        finished_at=None,
        error=None,
    )

    thread = threading.Thread(target=_run_backup_worker, args=(job_id, source, repository, "manual"), daemon=True)
    thread.start()
    return _snapshot_job()


@app.get("/api/jobs/history")
def job_history(limit: int = Query(default=20, ge=1, le=100)) -> dict:
    try:
        history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        if not isinstance(history, list):
            history = []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        history = []
    return {"items": history[:limit]}
