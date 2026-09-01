from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter

router = APIRouter(tags=["scheduler"])

STATE_PATH = Path(os.getenv("JAYN_VAULT_SCHEDULER_STATE", "/var/lib/jayn-vault/scheduler-state.json"))
CHECK_INTERVAL_SECONDS = max(2, int(os.getenv("JAYN_VAULT_SCHEDULER_INTERVAL", "5")))
GRACE_SECONDS = max(60, int(os.getenv("JAYN_VAULT_SCHEDULER_GRACE", "900")))
DEFAULT_TIMEZONE = "America/New_York"
WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_LOCK = threading.Lock()
_RUNTIME: dict = {
    "service": "stopped",
    "last_check": None,
    "last_trigger": None,
    "last_triggered_at": None,
    "last_error": None,
    "pending": None,
}


def _engine():
    # main.py is fully loaded by the time the FastAPI startup event fires.
    # Importing lazily avoids a circular import during module initialization.
    import main

    return main


def _runtime(**updates) -> dict:
    with _LOCK:
        _RUNTIME.update(updates)
        return dict(_RUNTIME)


def _runtime_snapshot() -> dict:
    with _LOCK:
        return dict(_RUNTIME)


def _load_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("last_fired", {})
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"last_fired": {}}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = STATE_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(temp, STATE_PATH)
    except OSError as exc:
        _runtime(last_error=f"Unable to persist scheduler state: {exc}")


def _parse_hhmm(value: str) -> tuple[int, int]:
    parsed = datetime.strptime(value, "%H:%M")
    return parsed.hour, parsed.minute


def _timezone(schedule: dict) -> ZoneInfo:
    name = str(schedule.get("timezone") or DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _most_recent_slot(trigger: str, schedule: dict, now: datetime) -> datetime | None:
    config = schedule.get(trigger) or {}
    if not config.get("enabled", True):
        return None

    hour, minute = _parse_hhmm(str(config.get("time") or ("06:00" if trigger == "daily" else "02:00")))

    if trigger == "daily":
        slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot > now:
            slot -= timedelta(days=1)
        return slot

    day = str(config.get("day") or "sunday").lower()
    target_weekday = WEEKDAYS.get(day, WEEKDAYS["sunday"])
    days_back = (now.weekday() - target_weekday) % 7
    slot = (now - timedelta(days=days_back)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=7)
    return slot


def _slot_key(trigger: str, slot: datetime) -> str:
    return f"{trigger}:{slot.isoformat()}"


def _schedule_conflict(schedule: dict) -> bool:
    daily = schedule.get("daily") or {}
    weekly = schedule.get("weekly") or {}
    return bool(
        daily.get("enabled", True)
        and weekly.get("enabled", True)
        and str(daily.get("time") or "06:00") == str(weekly.get("time") or "02:00")
    )


def _start_scheduled_backup(trigger: str) -> bool:
    engine = _engine()
    current = engine._snapshot_job()
    if current.get("status") == "running":
        return False

    data = engine._load_config()
    source_raw = data.get("source")
    destination_raw = data.get("destination")
    if not source_raw or not destination_raw:
        raise RuntimeError("Scheduled backup requires both a configured source and destination.")

    source = engine._resolve_dir(source_raw)
    repository = engine._resolve_dir(destination_raw)
    engine._validate_backup_paths(source, repository)

    if not os.access(source, os.R_OK | os.X_OK):
        raise RuntimeError("Configured source is not readable by JAYN Vault.")
    if not os.access(repository, os.W_OK | os.X_OK):
        raise RuntimeError("Configured destination is not writable by JAYN Vault.")

    started = engine._vault_now()
    job_id = uuid.uuid4().hex[:12]
    engine._set_job(
        id=job_id,
        status="running",
        phase="starting",
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

    thread = threading.Thread(
        target=engine._run_backup_worker,
        args=(job_id, source, repository, trigger),
        daemon=True,
        name=f"jayn-vault-{trigger}-{job_id}",
    )
    thread.start()
    return True


def _evaluate_once() -> None:
    engine = _engine()
    data = engine._load_config()
    schedule = data.get("schedule") or {}
    timezone = _timezone(schedule)
    now = datetime.now(timezone)
    _runtime(last_check=now.isoformat())

    if _schedule_conflict(schedule):
        _runtime(
            last_error="Daily and Weekly schedules use the same enabled time. Resolve the schedule conflict before automation can run.",
            pending=None,
        )
        return

    state = _load_state()
    last_fired = state.setdefault("last_fired", {})
    pending = _runtime_snapshot().get("pending")

    candidates: list[tuple[str, datetime, str]] = []
    for trigger in ("daily", "weekly"):
        slot = _most_recent_slot(trigger, schedule, now)
        if slot is None:
            continue
        key = _slot_key(trigger, slot)
        age = (now - slot).total_seconds()
        if last_fired.get(trigger) == key:
            continue
        if 0 <= age <= GRACE_SECONDS:
            candidates.append((trigger, slot, key))

    if pending:
        trigger = str(pending.get("trigger") or "")
        slot_text = str(pending.get("slot") or "")
        key = str(pending.get("key") or "")
        try:
            slot = datetime.fromisoformat(slot_text)
        except ValueError:
            _runtime(pending=None)
        else:
            if trigger in {"daily", "weekly"} and key and last_fired.get(trigger) != key:
                candidates.insert(0, (trigger, slot, key))

    seen: set[str] = set()
    for trigger, slot, key in candidates:
        if key in seen:
            continue
        seen.add(key)

        if engine._snapshot_job().get("status") == "running":
            _runtime(pending={"trigger": trigger, "slot": slot.isoformat(), "key": key}, last_error=None)
            return

        try:
            started = _start_scheduled_backup(trigger)
        except Exception as exc:
            _runtime(last_error=f"{trigger.title()} scheduler could not start backup: {exc}")
            return

        if not started:
            _runtime(pending={"trigger": trigger, "slot": slot.isoformat(), "key": key})
            return

        last_fired[trigger] = key
        state["last_fired"] = last_fired
        state["updated_at"] = now.isoformat()
        _save_state(state)
        _runtime(
            last_trigger=trigger,
            last_triggered_at=now.isoformat(),
            last_error=None,
            pending=None,
        )
        return

    if pending and not candidates:
        _runtime(pending=None)


def _loop() -> None:
    _runtime(service="running", last_error=None)
    while not _STOP.is_set():
        try:
            _evaluate_once()
        except Exception as exc:
            _runtime(last_error=f"Scheduler check failed: {exc}")
        _STOP.wait(CHECK_INTERVAL_SECONDS)
    _runtime(service="stopped")


@router.on_event("startup")
def start_scheduler() -> None:
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, daemon=True, name="jayn-vault-scheduler")
    _THREAD.start()


@router.on_event("shutdown")
def stop_scheduler() -> None:
    _STOP.set()
    thread = _THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=min(2.0, CHECK_INTERVAL_SECONDS))


@router.get("/api/scheduler/status")
def scheduler_status() -> dict:
    state = _load_state()
    runtime = _runtime_snapshot()
    try:
        engine = _engine()
        schedule = engine._schedule_payload(engine._load_config())
    except Exception:
        schedule = None
    return {
        **runtime,
        "check_interval_seconds": CHECK_INTERVAL_SECONDS,
        "grace_seconds": GRACE_SECONDS,
        "last_fired": state.get("last_fired", {}),
        "schedule": schedule,
    }
