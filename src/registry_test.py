"""Behaviour spec for registry.py, ported test-for-test from registry.test.ts,
plus coverage for the double-spawn guard the Python port added.

Run: uv run --with pytest pytest src/registry_test.py -q
"""

import json
from datetime import datetime, timezone

from registry import (
    build_self_entry,
    entry_path,
    read_entry,
    registry_dir,
    remove_entry,
    remove_own_entry,
    sweep,
    write_entry,
)


def entry(session_id: str, claude_pid: int) -> dict:
    return {
        "session_id": session_id,
        "claude_pid": claude_pid,
        "server_pid": 1000,
        "port": 49321,
        "cwd": "/tmp",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "name": None,
    }


class TestRegistry:
    def test_write_then_read_round_trips(self, tmp_path):
        write_entry(entry("abc", 42), tmp_path)
        assert read_entry("abc", tmp_path)["port"] == 49321

    def test_read_entry_returns_none_for_missing_session(self, tmp_path):
        assert read_entry("nope", tmp_path) is None

    def test_remove_entry_is_idempotent(self, tmp_path):
        write_entry(entry("abc", 42), tmp_path)
        remove_entry("abc", tmp_path)
        remove_entry("abc", tmp_path)
        assert not entry_path("abc", tmp_path).exists()

    def test_sweep_keeps_live_entries_and_removes_dead_ones(self, tmp_path):
        write_entry(entry("live", 100), tmp_path)
        write_entry(entry("dead", 200), tmp_path)
        removed = sweep(tmp_path, lambda pid: pid == 100)
        assert removed == ["dead.json"]
        assert read_entry("live", tmp_path) is not None
        assert read_entry("dead", tmp_path) is None

    def test_sweep_removes_unreadable_entries(self, tmp_path):
        registry_dir(tmp_path).mkdir(parents=True)
        (registry_dir(tmp_path) / "corrupt.json").write_text("{not json")
        assert sweep(tmp_path, lambda pid: True) == ["corrupt.json"]

    def test_sweep_on_missing_dir_returns_empty(self, tmp_path):
        assert sweep(tmp_path / "absent") == []

    def test_sweep_ignores_non_json_files(self, tmp_path):
        registry_dir(tmp_path).mkdir(parents=True)
        (registry_dir(tmp_path) / "README.txt").write_text("hello")
        assert sweep(tmp_path, lambda pid: False) == []


class TestIdentityHandling:
    def test_no_session_id_in_env_means_no_registration(self):
        assert build_self_entry({}, 1234, "/somewhere", 10, 9) is None

    def test_session_id_in_env_builds_a_complete_entry(self):
        built = build_self_entry(
            {"CLAUDE_CODE_SESSION_ID": "abc-123"}, 1234, "/somewhere", 10, 9
        )
        assert built == {
            "session_id": "abc-123",
            "claude_pid": 9,
            "server_pid": 10,
            "port": 1234,
            "cwd": "/somewhere",
            "started_at": built["started_at"],
            "name": None,
        }

    def test_empty_string_session_id_is_treated_as_absent(self):
        assert build_self_entry({"CLAUDE_CODE_SESSION_ID": ""}, 1, "/", 2, 3) is None


class TestDoubleSpawnGuard:
    def test_owner_removes_its_own_entry(self, tmp_path):
        e = entry("abc", 42)
        write_entry(e, tmp_path)
        assert remove_own_entry("abc", e["server_pid"], tmp_path) is True
        assert read_entry("abc", tmp_path) is None

    def test_superseded_instance_leaves_the_new_entry_alone(self, tmp_path):
        e = entry("abc", 42)
        e["server_pid"] = 2000  # the second spawn's registration
        write_entry(e, tmp_path)
        assert remove_own_entry("abc", 1000, tmp_path) is False
        assert read_entry("abc", tmp_path)["server_pid"] == 2000

    def test_missing_entry_is_a_no_op(self, tmp_path):
        assert remove_own_entry("ghost", 1000, tmp_path) is False
