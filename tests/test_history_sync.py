import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

import codex_duo as duo
import history_sync as sync


def record(kind, payload, timestamp="2026-10-09T10:00:00Z"):
    return json.dumps({"type": kind, "payload": payload, "timestamp": timestamp}).encode() + b"\n"


def meta(task_id="task", base=None):
    return record("session_meta", {"id": task_id, "session_id": task_id, "history_base": base})


def turn(text, timestamp="2026-10-09T10:00:00Z"):
    return (record("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]})
            + record("event_msg", {"type": "task_complete"}, timestamp))


class HistorySyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        for key, value in (("ROOT", self.root / "duo"), ("PRIMARY", self.root / "primary"),
                           ("SECONDARY", self.root / "secondary")):
            patch = mock.patch.object(duo, key, value)
            patch.start()
            self.addCleanup(patch.stop)

    def make_file(self, name, raw):
        path = self.root / name
        path.write_bytes(raw)
        return path

    def test_segment_boundary_and_in_progress_turn_are_excluded(self):
        first = meta() + turn("first")
        p1 = self.make_file("one.jsonl", first + turn("discarded branch"))
        p2 = self.make_file("two.jsonl", meta(base={"end_byte_offset": len(first)}) + turn("second")
                            + record("response_item", {"type": "message", "role": "user", "content": [{"text": "in progress"}]}))
        result = sync.completed_snapshot([p1, p2])
        encoded = sync.encode_snapshot(result, "new-id")
        self.assertIn(b"first", encoded)
        self.assertIn(b"second", encoded)
        self.assertNotIn(b"discarded branch", encoded)
        self.assertNotIn(b"in progress", encoded)
        self.assertNotIn(b"history_base", encoded)
        self.assertEqual(json.loads(encoded.splitlines()[0])["payload"]["id"], "new-id")

    def test_incomplete_first_turn_not_synced(self):
        path = self.make_file("one.jsonl", meta() + record("event_msg", {"type": "task_started"}))
        self.assertIsNone(sync.completed_snapshot([path]))

    def test_missing_fork_ancestry_is_reported_instead_of_truncated(self):
        path = self.make_file("fork.jsonl", meta(base={"thread_id": "missing", "end_byte_offset": 5}) + turn("later"))
        with self.assertRaises(ValueError):
            sync.completed_snapshot([path])

    def test_newer_divergent_content_overwrites_same_id(self):
        paths = {"primary": self.make_file("primary.jsonl", meta() + turn("account one")),
                 "secondary": self.make_file("secondary.jsonl", meta() + turn("account two", "2026-10-09T11:00:00Z"))}
        task = {"task": {"id": "task", "title": "A task", "roots": ["/project"]}}
        with mock.patch.object(sync, "project_tasks", return_value=task.copy()):
            with mock.patch.object(duo, "session_files", side_effect=lambda p, _: [paths[p]]):
                with mock.patch.object(sync, "install_original") as install, \
                        mock.patch.object(duo, "desktop_pids", return_value={"primary": None, "secondary": None}):
                    first = sync.synchronize(duo)
        install.assert_called_once()
        self.assertEqual(install.call_args.args[1], "primary")
        self.assertEqual(install.call_args.args[2]["id"], "task")
        self.assertEqual(first["updated"], 1)
        self.assertEqual(first["pending"], [])
        self.assertEqual(json.loads((duo.ROOT / "conversation-sync.json").read_text())["copies"], {})

    def test_import_time_cannot_make_older_content_win(self):
        old = self.make_file("older.jsonl", meta() + turn("older", "2026-10-09T09:00:00Z"))
        new = self.make_file("newer.jsonl", meta() + turn("newer", "2026-10-09T10:00:00Z"))
        snapshot = sync.completed_snapshot([old])
        imported = self.make_file("just-imported.jsonl", sync.encode_snapshot(snapshot, "task"))
        self.assertEqual(sync.latest_key(snapshot), sync.latest_key(sync.completed_snapshot([imported])))
        self.assertGreater(sync.latest_key(sync.completed_snapshot([new])), sync.latest_key(snapshot))

    def test_busy_windows_wait_without_restart_or_overwrite(self):
        paths = {"primary": self.make_file("primary.jsonl", meta() + turn("old")),
                 "secondary": self.make_file("secondary.jsonl", meta() + turn("new", "2026-10-09T11:00:00Z"))}
        task = {"task": {"id": "task", "title": "task", "roots": ["/project"]}}
        with mock.patch.object(sync, "project_tasks", return_value=task), \
                mock.patch.object(duo, "session_files", side_effect=lambda p, _: [paths[p]]), \
                mock.patch.object(duo, "desktop_pids", return_value={"primary": 1, "secondary": 2}), \
                mock.patch.object(sync, "profile_has_running_task", return_value=True), \
                mock.patch.object(duo, "stop_profile") as stop, \
                mock.patch.object(sync, "install_original") as install:
            result = sync.synchronize(duo, auto_refresh=True)
        self.assertEqual(result["updated"], 0)
        self.assertTrue(result["pending"])
        stop.assert_not_called()
        install.assert_not_called()

    def test_one_click_refreshes_only_receiver_and_keeps_id(self):
        paths = {"primary": self.make_file("primary.jsonl", meta() + turn("old")),
                 "secondary": self.make_file("secondary.jsonl", meta() + turn("new", "2026-10-09T11:00:00Z"))}
        task = {"task": {"id": "task", "title": "task", "roots": ["/project"]}}
        pids = {"primary": 1, "secondary": 2}
        def stop(profile): pids[profile] = None
        def reopen(profile): pids[profile] = 3
        with mock.patch.object(sync, "project_tasks", return_value=task), \
                mock.patch.object(duo, "session_files", side_effect=lambda p, _: [paths[p]]), \
                mock.patch.object(duo, "desktop_pids", side_effect=lambda: pids.copy()), \
                mock.patch.object(sync, "profile_has_running_task", return_value=False), \
                mock.patch.object(duo, "stop_profile", side_effect=stop) as stopped, \
                mock.patch.object(duo, "open_profile", side_effect=reopen) as opened, \
                mock.patch.object(sync, "install_original") as install:
            result = sync.synchronize(duo, auto_refresh=True)
        stopped.assert_called_once_with("primary")
        opened.assert_called_once_with("primary")
        self.assertEqual(install.call_args.args[2]["id"], "task")
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["reloadProfiles"], ["primary"])

    def test_waiting_click_is_retried_without_another_click(self):
        with mock.patch.object(duo, "sync_desktop_projects", return_value={"created": [], "total": 2}), \
                mock.patch.object(sync, "synchronize", side_effect=[{"pending": [{"reason": "busy"}]}, {"pending": []}]) as history:
            duo.sync_desktop_workspace(refresh_windows=True)
            self.assertTrue((duo.ROOT / "pending-workspace-sync.json").exists())
            duo.sync_desktop_workspace()
        self.assertTrue(all(call.kwargs["auto_refresh"] for call in history.call_args_list))
        self.assertFalse((duo.ROOT / "pending-workspace-sync.json").exists())

    def test_receive_updates_existing_id_and_repeat_is_unchanged(self):
        paths = {"primary": self.make_file("primary.jsonl", meta() + turn("first")),
                 "secondary": self.make_file("secondary.jsonl", meta() + turn("first") + turn("new"))}
        task = {"task": {"id": "task", "title": "A task", "roots": ["/project"]}}
        def install(backend, target, task, snapshot):
            paths[target].write_bytes(paths["secondary"].read_bytes())
            self.assertEqual(task["id"], "task")
        with mock.patch.object(sync, "project_tasks", return_value=task.copy()), \
                mock.patch.object(duo, "session_files", side_effect=lambda p, _: [paths[p]]), \
                mock.patch.object(duo, "desktop_pids", return_value={"primary": None, "secondary": 2}), \
                mock.patch.object(sync, "install_original", side_effect=install):
            first = sync.synchronize(duo, receiver="primary")
            second = sync.synchronize(duo, receiver="primary")
        self.assertEqual(first["updated"], 1)
        self.assertEqual(first["copies"][0]["id"], "task")
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(json.loads((duo.ROOT / "conversation-sync.json").read_text())["copies"], {})

    def test_unchanged_generated_copies_are_not_echoed(self):
        path = self.make_file("copy.jsonl", meta() + turn("same"))
        snapshot = sync.completed_snapshot([path])
        duo.atomic_write(duo.ROOT / "conversation-sync.json", json.dumps({"copies": {
            "primary:secondary:original": {"id": "copy", "signature": snapshot["signature"]}}}).encode())
        with mock.patch.object(sync, "project_tasks", return_value={}) as inventory:
            with mock.patch.object(duo, "session_files", return_value=[path]):
                with mock.patch.object(sync, "install_snapshot") as install:
                    result = sync.synchronize(duo)
        install.assert_not_called()
        self.assertEqual(result["updated"], 0)
        self.assertEqual(inventory.call_args.args[2], {"copy"})

    def test_original_cannot_be_replaced_while_receiver_running(self):
        with mock.patch.object(duo, "desktop_pids", return_value={"primary": 42}):
            with self.assertRaises(RuntimeError):
                sync.install_original(duo, "primary", {"id": "task"}, {})

    def test_failed_original_import_restores_files_and_metadata(self):
        duo.PRIMARY.mkdir(parents=True)
        state = duo.PRIMARY / ".codex-global-state.json"
        state.write_text('{"unrelated": "keep"}')
        old = duo.PRIMARY / "sessions/rollout-task.jsonl"
        duo.atomic_write(old, b"original history\n")
        db_path = duo.PRIMARY / "state_5.sqlite"
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, history_mode TEXT)")
        db.execute("INSERT INTO threads VALUES (?, ?, ?)", ("task", str(old), "paginated"))
        db.commit()
        db.close()
        with mock.patch.object(duo, "desktop_pids", return_value={"primary": None}), \
                mock.patch.object(sync, "file_in_use", return_value=False), \
                mock.patch.object(sync, "install_snapshot", side_effect=RuntimeError("failed import")):
            with self.assertRaises(RuntimeError):
                sync.install_original(duo, "primary", {"id": "task"}, {})
        self.assertEqual(old.read_bytes(), b"original history\n")
        self.assertEqual(json.loads(state.read_text()), {"unrelated": "keep"})
        db = sqlite3.connect(db_path)
        self.assertEqual(db.execute("SELECT rollout_path FROM threads").fetchone()[0], str(old))
        db.close()

    def test_edited_copy_is_not_overwritten(self):
        path = duo.SECONDARY / "sessions/copy.jsonl"
        duo.atomic_write(path, b"user edited\n")
        entry = {"id": "copy", "path": "sessions/copy.jsonl", "fileDigest": sync.digest(b"old\n")}
        with mock.patch.object(duo, "session_files", return_value=[path]):
            self.assertEqual(sync.install_snapshot(duo, "secondary", {}, {}, entry, "account"), "edited")
        self.assertEqual(path.read_bytes(), b"user edited\n")

    def test_open_copy_is_not_replaced(self):
        path = duo.SECONDARY / "sessions/copy.jsonl"
        duo.atomic_write(path, b"existing\n")
        entry = {"id": "copy", "path": "sessions/copy.jsonl", "fileDigest": sync.digest(path.read_bytes())}
        with mock.patch.object(sync, "file_in_use", return_value=True):
            with mock.patch.object(duo, "session_files", return_value=[path]):
                self.assertEqual(sync.install_snapshot(duo, "secondary", {}, {}, entry, "account"), "open")

    def test_new_local_segment_is_never_overwritten(self):
        path = duo.SECONDARY / "sessions/copy.jsonl"
        duo.atomic_write(path, b"original unchanged\n")
        entry = {"id": "copy", "path": "sessions/copy.jsonl", "fileDigest": sync.digest(path.read_bytes())}
        with mock.patch.object(duo, "session_files", return_value=[path, path.with_name("continued.jsonl")]):
            self.assertEqual(sync.install_snapshot(duo, "secondary", {}, {}, entry, "account"), "edited")

    def test_workspace_command_includes_history_sync(self):
        with mock.patch.object(duo, "sync_desktop_projects", return_value={"created": [], "total": 2}):
            with mock.patch.object(sync, "synchronize", return_value={"updated": 1}) as history:
                result = duo.sync_desktop_workspace()
        history.assert_called_once()
        self.assertEqual(result["history"]["updated"], 1)


if __name__ == "__main__":
    unittest.main()
