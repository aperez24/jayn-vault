from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/restore", tags=["restore"])

CONFIG_PATH = Path(os.getenv("JAYN_VAULT_CONFIG", "/var/lib/jayn-vault/config.json"))
DEFAULT_TIMEZONE = "America/New_York"
REPOSITORY_META_DIR = ".jayn-vault"
SNAPSHOTS_DIR = "snapshots"
SNAPSHOT_SUFFIXES = ("_manual", "_daily", "_weekly")


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
