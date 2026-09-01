from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/restore", tags=["restore"])

CONFIG_PATH = Path(os.getenv("JAYN_VAULT_CONFIG", "/var/lib/jayn-vault/config.json"))
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_STORAGE_ROOTS = [
    {"id": "jaynos", "name": "jaynOS", "path": "/mnt/jayn-vault/sources/jaynos"},
]
REPOSITORY_META_DIR = ".jayn-vault"
SNAPSHOTS_DIR = "snapshots"
SNAPSHOT_SUFFIXES = ("_manual", "_daily", "_weekly")

_RECOVERY_LOCK = threading.Lock()
_RECOVERY_STATE: dict = {
    "id": None,
    "status": "idle",
    "snapshot": None,
    "destination": None,
    "recovery_folder": None,
    "selected_items": 0,
    "total_files": 0,
    "copied_files": 0,
    "copied_bytes": 0,
    "current_file": None,
    "started_at": None,
    "finished_at": None,
    "error": None,
}


class RecoveryRequest(BaseModel):
    snapshot: str
    paths: list[str] = Field(min_length=1, max_length=500)
    destination: str


class RecoveryFolderRequest(BaseModel):
    parent: str
    name: str = Field(min_length=1, max_length=120)


def _load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _timezone() -> ZoneInfo:
    name = str((_load_config().get("schedule") or {}).get("timezone") or DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _now() -> datetime:
    return datetime.now(_timezone())


def _repository() -> Path:
    raw = _load_config().get("destination")
    if not raw:
        raise HTTPException(status_code=400, detail="No JAYN Vault destination is configured.")
    repository = Path(str(raw)).expanduser()
    try:
        repository = repository.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=404, detail="The configured JAYN Vault repository is unavailable.") from exc
    if not repository.is_dir():
        raise HTTPException(status_code=400, detail="The configured destination is not a directory.")
    return repository


def _storage_roots() -> list[Path]:
    roots = DEFAULT_STORAGE_ROOTS
    raw = os.getenv("JAYN_VAULT_STORAGE_ROOTS")
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                roots = parsed
        except json.JSONDecodeError:
            pass

    result: list[Path] = []
    for item in roots:
        try:
            path = Path(str(item.get("path", ""))).expanduser().resolve(strict=True)
            if path.is_dir():
                result.append(path)
        except (FileNotFoundError, RuntimeError, OSError):
            continue
    return result


def _safe_recovery_destination(raw: str) -> Path:
    try:
        destination = Path(raw).expanduser().resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=404, detail="The selected recovery destination is unavailable.") from exc
    if not destination.is_dir():
        raise HTTPException(status_code=400, detail="Recovery destination must be a directory.")
    if not os.access(destination, os.W_OK | os.X_OK):
        raise HTTPException(status_code=403, detail="JAYN Vault cannot write to the selected recovery destination.")

    allowed = False
    for root in _storage_roots():
        try:
            destination.relative_to(root)
            allowed = True
            break
        except ValueError:
            continue
    if not allowed:
        raise HTTPException(status_code=403, detail="Recovery destination must be inside an approved JAYN Vault storage location.")

    repository = _repository()
    snapshots = repository / SNAPSHOTS_DIR
    try:
        destination.relative_to(snapshots)
        raise HTTPException(status_code=400, detail="Recovery files cannot be written inside the snapshot repository.")
    except ValueError:
        pass
    return destination


def _safe_new_folder_name(raw: str) -> str:
    name = raw.strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Enter a valid folder name.")
    if "/" in name or "\\" in name or "\x00" in name:
        raise HTTPException(status_code=400, detail="Folder names cannot contain path separators.")
    if name == REPOSITORY_META_DIR:
        raise HTTPException(status_code=400, detail="That folder name is reserved by JAYN Vault.")
    return name


def _snapshots_root() -> Path:
    return _repository() / SNAPSHOTS_DIR


def _manifest(snapshot: Path) -> dict | None:
    path = snapshot / REPOSITORY_META_DIR / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("status") == "completed":
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return None


def _completed_snapshots() -> list[tuple[Path, dict]]:
    root = _snapshots_root()
    if not root.exists():
        return []
    result: list[tuple[Path, dict]] = []
    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Unable to read snapshot repository: {exc}") from exc
    for entry in entries:
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not any(entry.name.endswith(suffix) or f"{suffix}_" in entry.name for suffix in SNAPSHOT_SUFFIXES):
            continue
        manifest = _manifest(entry)
        if manifest is not None:
            result.append((entry, manifest))
    result.sort(key=lambda pair: pair[0].name, reverse=True)
    return result


def _snapshot_by_name(name: str) -> tuple[Path, dict]:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid snapshot name.")
    for snapshot, manifest in _completed_snapshots():
        if snapshot.name == name:
            return snapshot, manifest
    raise HTTPException(status_code=404, detail="Snapshot not found.")


def _relative_path(raw: str | None) -> PurePosixPath:
    text = (raw or "").strip().replace("\\", "/").strip("/")
    if not text:
        return PurePosixPath(".")
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(status_code=400, detail="Invalid restore path.")
    if REPOSITORY_META_DIR in path.parts:
        raise HTTPException(status_code=403, detail="Repository metadata is not browsable through Restore.")
    return path


def _inside_snapshot(snapshot: Path, relative: PurePosixPath) -> Path:
    candidate = snapshot if str(relative) == "." else snapshot.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(snapshot.resolve(strict=True))
    except (FileNotFoundError, ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(status_code=404, detail="The requested restore path does not exist in this snapshot.") from exc
    return resolved


def _iso_mtime(stat_result) -> str:
    return datetime.fromtimestamp(stat_result.st_mtime, tz=_timezone()).isoformat()


def _snapshot_payload(snapshot: Path, manifest: dict) -> dict:
    return {
        "name": snapshot.name,
        "trigger": manifest.get("trigger"),
        "started_at": manifest.get("started_at"),
        "finished_at": manifest.get("finished_at"),
        "previous_snapshot": manifest.get("previous_snapshot"),
        "total_files": int(manifest.get("total_files") or 0),
        "logical_bytes": int(manifest.get("logical_bytes") or 0),
        "physical_bytes_written": int(manifest.get("physical_bytes_written") or 0),
        "copied_files": int(manifest.get("copied_files") or 0),
        "reused_files": int(manifest.get("reused_files") or 0),
    }


def _set_recovery(**updates) -> dict:
    with _RECOVERY_LOCK:
        _RECOVERY_STATE.update(updates)
        return dict(_RECOVERY_STATE)


def _recovery_state() -> dict:
    with _RECOVERY_LOCK:
        return dict(_RECOVERY_STATE)


def _dedupe_selected(paths: list[PurePosixPath]) -> list[PurePosixPath]:
    unique: list[PurePosixPath] = []
    for path in sorted(set(paths), key=lambda item: (len(item.parts), item.as_posix())):
        if any(path == parent or parent in path.parents for parent in unique):
            continue
        unique.append(path)
    return unique


def _count_recovery_files(snapshot: Path, selections: list[PurePosixPath]) -> int:
    count = 0
    for relative in selections:
        source = _inside_snapshot(snapshot, relative)
        if source.is_file():
            count += 1
        elif source.is_dir():
            for root, dirs, files in os.walk(source, followlinks=False):
                dirs[:] = [name for name in dirs if name != REPOSITORY_META_DIR and not (Path(root) / name).is_symlink()]
                for name in files:
                    file_path = Path(root) / name
                    if not file_path.is_symlink():
                        count += 1
    return count


def _copy_recovery_file(source: Path, target: Path, display: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _set_recovery(current_file=display)
    shutil.copy2(source, target, follow_symlinks=False)
    state = _recovery_state()
    _set_recovery(
        copied_files=int(state.get("copied_files") or 0) + 1,
        copied_bytes=int(state.get("copied_bytes") or 0) + source.stat().st_size,
    )


def _run_recovery(job_id: str, snapshot: Path, snapshot_name: str, selections: list[PurePosixPath], destination: Path) -> None:
    started = _now()
    recovery_folder: Path | None = None
    try:
        total_files = _count_recovery_files(snapshot, selections)
        base = f"JAYN-Vault-Recovery_{started.strftime('%Y-%m-%d_%H-%M-%S')}"
        recovery_folder = destination / base
        counter = 1
        while recovery_folder.exists():
            recovery_folder = destination / f"{base}_{counter:02d}"
            counter += 1
        recovery_folder.mkdir(parents=False, exist_ok=False)

        _set_recovery(
            id=job_id,
            status="running",
            snapshot=snapshot_name,
            destination=str(destination),
            recovery_folder=str(recovery_folder),
            selected_items=len(selections),
            total_files=total_files,
            copied_files=0,
            copied_bytes=0,
            current_file=None,
            started_at=started.isoformat(),
            finished_at=None,
            error=None,
        )

        for relative in selections:
            source = _inside_snapshot(snapshot, relative)
            target = recovery_folder.joinpath(*relative.parts)
            if source.is_file():
                _copy_recovery_file(source, target, relative.as_posix())
                continue
            if not source.is_dir():
                continue

            target.mkdir(parents=True, exist_ok=True)
            for root, dirs, files in os.walk(source, followlinks=False):
                root_path = Path(root)
                dirs[:] = [name for name in dirs if name != REPOSITORY_META_DIR and not (root_path / name).is_symlink()]
                relative_root = root_path.relative_to(snapshot)
                for dirname in dirs:
                    recovery_folder.joinpath(*relative_root.parts, dirname).mkdir(parents=True, exist_ok=True)
                for name in files:
                    source_file = root_path / name
                    if source_file.is_symlink():
                        continue
                    file_relative = source_file.relative_to(snapshot)
                    target_file = recovery_folder.joinpath(*file_relative.parts)
                    _copy_recovery_file(source_file, target_file, file_relative.as_posix())

        _set_recovery(status="completed", current_file=None, finished_at=_now().isoformat())
    except Exception as exc:
        _set_recovery(status="failed", current_file=None, finished_at=_now().isoformat(), error=str(exc))


@router.get("/snapshots")
def list_restore_points() -> dict:
    snapshots = _completed_snapshots()
    return {
        "count": len(snapshots),
        "retention": "manual-only",
        "automatic_deletion": False,
        "items": [_snapshot_payload(snapshot, manifest) for snapshot, manifest in snapshots],
    }


@router.get("/browse")
def browse_snapshot(snapshot: str, path: str = Query(default="")) -> dict:
    snapshot_root, manifest = _snapshot_by_name(snapshot)
    relative = _relative_path(path)
    directory = _inside_snapshot(snapshot_root, relative)
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="The requested restore path is not a directory.")

    items: list[dict] = []
    try:
        entries = list(directory.iterdir())
    except (PermissionError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Unable to browse snapshot: {exc}") from exc

    for entry in entries:
        if entry.name == REPOSITORY_META_DIR:
            continue
        try:
            stat = entry.stat()
            kind = "directory" if entry.is_dir() else "file" if entry.is_file() else "other"
            rel = entry.relative_to(snapshot_root).as_posix()
            items.append(
                {
                    "name": entry.name,
                    "path": rel,
                    "type": kind,
                    "size": stat.st_size if kind == "file" else None,
                    "modified_at": _iso_mtime(stat),
                }
            )
        except (FileNotFoundError, PermissionError, OSError):
            continue

    items.sort(key=lambda item: (item["type"] != "directory", item["name"].casefold()))
    current = "" if str(relative) == "." else relative.as_posix()
    parent = ""
    if current:
        parent_path = PurePosixPath(current).parent
        parent = "" if str(parent_path) == "." else parent_path.as_posix()

    return {
        "snapshot": _snapshot_payload(snapshot_root, manifest),
        "path": current,
        "parent": parent,
        "items": items,
    }


@router.get("/versions")
def file_versions(path: str) -> dict:
    relative = _relative_path(path)
    if str(relative) == ".":
        raise HTTPException(status_code=400, detail="A file path is required.")

    versions: list[dict] = []
    previous_signature: tuple[int, int] | None = None
    for snapshot, manifest in _completed_snapshots():
        candidate = snapshot.joinpath(*relative.parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(snapshot.resolve(strict=True))
            if not resolved.is_file():
                continue
            stat = resolved.stat()
        except (FileNotFoundError, ValueError, RuntimeError, PermissionError, OSError):
            continue

        signature = (stat.st_size, stat.st_mtime_ns)
        versions.append(
            {
                "snapshot": snapshot.name,
                "trigger": manifest.get("trigger"),
                "backup_at": manifest.get("finished_at") or manifest.get("started_at"),
                "size": stat.st_size,
                "modified_at": _iso_mtime(stat),
                "different_from_newer": previous_signature is not None and signature != previous_signature,
            }
        )
        previous_signature = signature

    return {"path": relative.as_posix(), "count": len(versions), "items": versions}


@router.post("/recovery/folder")
def create_recovery_folder(request: RecoveryFolderRequest) -> dict:
    parent = _safe_recovery_destination(request.parent)
    name = _safe_new_folder_name(request.name)
    target = parent / name
    if target.exists():
        raise HTTPException(status_code=409, detail="A folder with that name already exists here.")
    try:
        target.mkdir(parents=False, exist_ok=False)
        resolved = target.resolve(strict=True)
        resolved.relative_to(parent.resolve(strict=True))
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="A folder with that name already exists here.") from exc
    except (PermissionError, OSError, ValueError, RuntimeError) as exc:
        try:
            if target.exists() and target.is_dir() and not any(target.iterdir()):
                target.rmdir()
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"Unable to create recovery folder: {exc}") from exc
    return {"name": name, "path": str(resolved), "parent": str(parent)}


@router.post("/recover")
def recover_to_alternate_location(request: RecoveryRequest) -> dict:
    current = _recovery_state()
    if current.get("status") == "running":
        raise HTTPException(status_code=409, detail="A recovery operation is already running.")

    snapshot, _ = _snapshot_by_name(request.snapshot)
    destination = _safe_recovery_destination(request.destination)
    selections = _dedupe_selected([_relative_path(path) for path in request.paths])
    if any(str(path) == "." for path in selections):
        raise HTTPException(status_code=400, detail="Select files or folders instead of the snapshot metadata root.")
    for relative in selections:
        _inside_snapshot(snapshot, relative)

    job_id = uuid.uuid4().hex[:12]
    _set_recovery(
        id=job_id,
        status="starting",
        snapshot=snapshot.name,
        destination=str(destination),
        recovery_folder=None,
        selected_items=len(selections),
        total_files=0,
        copied_files=0,
        copied_bytes=0,
        current_file=None,
        started_at=_now().isoformat(),
        finished_at=None,
        error=None,
    )
    thread = threading.Thread(
        target=_run_recovery,
        args=(job_id, snapshot, snapshot.name, selections, destination),
        daemon=True,
    )
    thread.start()
    return _recovery_state()


@router.get("/recovery/current")
def current_recovery() -> dict:
    return _recovery_state()
