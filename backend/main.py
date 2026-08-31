from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI(title="JAYN Vault API", version="0.1.0")

CONFIG_PATH = Path(os.getenv("JAYN_VAULT_CONFIG", "/var/lib/jayn-vault/config.json"))
DEFAULT_STORAGE_ROOTS = [
    {"id": "jaynos", "name": "jaynOS", "path": "/mnt/jayn-vault/sources/jaynos"},
]


class SelectionRequest(BaseModel):
    kind: Literal["source", "destination"]
    path: str


class SelectionState(BaseModel):
    source: str | None = None
    destination: str | None = None


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"source": None, "destination": None}
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Unable to read Vault configuration: {exc}") from exc


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
    return {
        "source": data.get("source"),
        "destination": data.get("destination"),
    }


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

    return {
        "source": data.get("source"),
        "destination": data.get("destination"),
    }
