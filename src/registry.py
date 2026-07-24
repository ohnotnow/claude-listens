"""Session registry: one JSON file per live Claude Code session, keyed by
session id, under ~/.claude-voice/registry/. Shared contract lives in the
ait Phase 1 epic (handy-UkLWZ.2); Python port of registry.ts (behaviour spec:
registry_test.py, ported from registry.test.ts)."""

import json
import os
from pathlib import Path


def default_base_dir() -> Path:
    return Path.home() / ".claude-voice"


def registry_dir(base: Path | None = None) -> Path:
    return (base or default_base_dir()) / "registry"


def ensure_dirs(base: Path | None = None) -> None:
    registry_dir(base).mkdir(parents=True, exist_ok=True, mode=0o700)


def entry_path(session_id: str, base: Path | None = None) -> Path:
    return registry_dir(base) / f"{session_id}.json"


def write_entry(entry: dict, base: Path | None = None) -> None:
    ensure_dirs(base)
    entry_path(entry["session_id"], base).write_text(json.dumps(entry, indent=2) + "\n")


def read_entry(session_id: str, base: Path | None = None) -> dict | None:
    try:
        return json.loads(entry_path(session_id, base).read_text())
    except Exception:
        return None


def remove_entry(session_id: str, base: Path | None = None) -> None:
    entry_path(session_id, base).unlink(missing_ok=True)


def remove_own_entry(session_id: str, server_pid: int, base: Path | None = None) -> bool:
    """Remove the entry only if this server still owns it.

    Claude Code spawns the server twice at session start (observed 2026-07-23,
    ant handy-C8ggs); the first instance's shutdown must not delete the entry
    the second instance just wrote. Returns True if an entry was removed.
    """
    current = read_entry(session_id, base)
    if current is None or current.get("server_pid") != server_pid:
        return False
    remove_entry(session_id, base)
    return True


def build_self_entry(
    env: dict, port: int, cwd: str, server_pid: int, claude_pid: int
) -> dict | None:
    """Identity handling: decide this server's registry entry from its spawn
    environment. Returns None when there is no owning session to register
    against (e.g. run standalone for a smoke test)."""
    session_id = env.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        return None
    from datetime import datetime, timezone

    return {
        "session_id": session_id,
        "claude_pid": claude_pid,
        "server_pid": server_pid,
        "port": port,
        "cwd": cwd,
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "name": None,  # reserved for Phase 2 named sessions
    }


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def sweep(base: Path | None = None, is_alive=pid_alive) -> list[str]:
    """Remove entries whose owning claude process is gone (or that are
    unreadable). Returns the removed filenames. `is_alive` is injectable for
    tests."""
    directory = registry_dir(base)
    if not directory.is_dir():
        return []
    removed: list[str] = []
    for path in sorted(directory.iterdir()):
        if path.suffix != ".json":
            continue
        dead = True
        try:
            entry = json.loads(path.read_text())
            pid = entry.get("claude_pid")
            dead = not isinstance(pid, int) or not is_alive(pid)
        except Exception:
            pass  # unreadable entry: treat as dead
        if dead:
            path.unlink(missing_ok=True)
            removed.append(path.name)
    return removed
