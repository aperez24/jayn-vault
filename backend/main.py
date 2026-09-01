from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

app = FastAPI(title="JAYN Vault API", version="0.2.0")

CONFIG_PATH = Path(os.getenv("JAYN_VAULT_CONFIG", "/var/lib/jayn-vault/config.json"))
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
