from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

app = FastAPI(title="JAYN Vault API", version="0.3.0")

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

_JOB_LOCK = threading.Lock()
_JOB_STATE: dict = {
    "id": None,
    "status": "idle",
    "phase": "idle",
    "percent": 0.0,
    "source": None,
    "destination": None,
    "total_files": 0,
    "processed_files": 0,
    "copied_files": 0,
    "skipped_files": 0,
    "failed_files": 0,
    "total_bytes": 0,
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


def _directory_size(directory: Path) -> tuple[int, int, int]:
    total_bytes = 0
    file_count = 0
    directory_count = 0

    for root, dirs, files in os.walk(directory, followlinks=False):
        directory_count += len(dirs)
        for filename in files:
            path = Path(root) / filename
            try:
                if path.is_symlink():
                    continue
                total_bytes += path.stat().st_size
                file_count += 1
            except (PermissionError, FileNotFoundError, OSError):
                continue

    return total_bytes, file_count, directory_count


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


def _scan_source(source: Path) -> tuple[list[tuple[Path, str, int, int]], list[str], int]:
    files: list[tuple[Path, str, int, int]] = []
    directories: list[str] = []
    total_bytes = 0
    for root, dirs, names in os.walk(source, followlinks=False):
        root_path = Path(root)
        relative_root = root_path.relative_to(source)
        for dirname in dirs:
            directory = root_path / dirname
            if directory.is_symlink():
                continue
            directories.append(str((relative_root / dirname).as_posix()))
        for name in names:
            path = root_path / name
            try:
                if path.is_symlink():
                    continue
                stat = path.stat()
            except (PermissionError, FileNotFoundError, OSError):
                continue
            relative = str(path.relative_to(source).as_posix())
            files.append((path, relative, stat.st_size, stat.st_mtime_ns))
            total_bytes += stat.st_size
    return files, directories, total_bytes


def _copy_file_with_progress(source_file: Path, destination_file: Path, file_size: int) -> int:
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
                _set_job(
                    processed_bytes=int(_snapshot_job().get("processed_bytes") or 0) + len(chunk),
                    copied_bytes=int(_snapshot_job().get("copied_bytes") or 0) + len(chunk),
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


def _run_backup_worker(job_id: str, source: Path, destination: Path) -> None:
    started = datetime.now().astimezone()
    try:
        _set_job(
            id=job_id,
            status="running",
            phase="scanning",
            percent=0.0,
            source=str(source),
            destination=str(destination),
            total_files=0,
            processed_files=0,
            copied_files=0,
            skipped_files=0,
            failed_files=0,
            total_bytes=0,
            processed_bytes=0,
            copied_bytes=0,
            current_file=None,
            started_at=started.isoformat(),
            finished_at=None,
            error=None,
        )

        files, directories, total_bytes = _scan_source(source)
        usage = shutil.disk_usage(destination)
        if usage.free < total_bytes:
            raise RuntimeError(f"Destination does not have enough free space. Short by {total_bytes - usage.free} bytes.")

        for relative in directories:
            (destination / relative).mkdir(parents=True, exist_ok=True)

        _set_job(phase="copying", total_files=len(files), total_bytes=total_bytes)

        for source_file, relative, file_size, source_mtime_ns in files:
            destination_file = destination / relative
            _set_job(current_file=relative)

            try:
                unchanged = False
                if destination_file.exists() and destination_file.is_file():
                    try:
                        dest_stat = destination_file.stat()
                        unchanged = dest_stat.st_size == file_size and dest_stat.st_mtime_ns == source_mtime_ns
                    except OSError:
                        unchanged = False

                state = _snapshot_job()
                if unchanged:
                    _set_job(
                        processed_bytes=int(state.get("processed_bytes") or 0) + file_size,
                        processed_files=int(state.get("processed_files") or 0) + 1,
                        skipped_files=int(state.get("skipped_files") or 0) + 1,
                    )
                else:
                    _copy_file_with_progress(source_file, destination_file, file_size)
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
        final = _set_job(status="completed", phase="complete", percent=100.0, current_file=None, finished_at=finished.isoformat())
        _append_history(final)
    except Exception as exc:
        finished = datetime.now().astimezone()
        final = _set_job(status="failed", phase="failed", current_file=None, finished_at=finished.isoformat(), error=str(exc))
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

    if source_raw:
        source_path = _resolve_dir(source_raw)
        if not os.access(source_path, os.R_OK | os.X_OK):
            raise HTTPException(status_code=403, detail="The configured source is not readable.")
        total_bytes, file_count, directory_count = _directory_size(source_path)
        source = {"path": str(source_path), "bytes": total_bytes, "files": file_count, "directories": directory_count}

    if destination_raw:
        destination_path = _resolve_dir(destination_raw)
        if not os.access(destination_path, os.W_OK | os.X_OK):
            raise HTTPException(status_code=403, detail="The configured destination is not writable.")
        usage = shutil.disk_usage(destination_path)
        destination = {
            "path": str(destination_path),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
        }

    capacity_ok = None
    shortfall_bytes = 0
    if source is not None and destination is not None:
        capacity_ok = destination["free_bytes"] >= source["bytes"]
        if not capacity_ok:
            shortfall_bytes = source["bytes"] - destination["free_bytes"]

    return {"source": source, "destination": destination, "capacity_ok": capacity_ok, "shortfall_bytes": shortfall_bytes}


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
    destination = _resolve_dir(destination_raw)
    _validate_backup_paths(source, destination)

    if not os.access(source, os.R_OK | os.X_OK):
        raise HTTPException(status_code=403, detail="The configured source is not readable.")
    if not os.access(destination, os.W_OK | os.X_OK):
        raise HTTPException(status_code=403, detail="The configured destination is not writable.")

    total_bytes, _, _ = _directory_size(source)
    usage = shutil.disk_usage(destination)
    if usage.free < total_bytes:
        raise HTTPException(status_code=409, detail=f"Destination does not have enough free space. Short by {total_bytes - usage.free} bytes.")

    job_id = uuid.uuid4().hex[:12]
    _set_job(
        id=job_id,
        status="running",
        phase="starting",
        percent=0.0,
        source=str(source),
        destination=str(destination),
        total_files=0,
        processed_files=0,
        copied_files=0,
        skipped_files=0,
        failed_files=0,
        total_bytes=total_bytes,
        processed_bytes=0,
        copied_bytes=0,
        current_file=None,
        started_at=datetime.now().astimezone().isoformat(),
        finished_at=None,
        error=None,
    )

    thread = threading.Thread(target=_run_backup_worker, args=(job_id, source, destination), daemon=True)
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
