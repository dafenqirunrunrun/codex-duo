import pathlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest import mock

import codex_duo as duo


class DuoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        env_patch = mock.patch.dict(duo.os.environ, {"CLAUDE_CONFIG_DIR": str(root / "claude-config")})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        for attr, value in (("ROOT", root / "duo"), ("PRIMARY", root / "primary"),
                            ("SECONDARY", root / "duo/second-home"),
                            ("SECONDARY_DATA", root / "duo/second-desktop-data")):
            patcher = mock.patch.object(duo, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_names_are_local_and_separate(self):
        duo.rename("primary", "个人")
        duo.rename("secondary", "工作")
        self.assertEqual(duo.settings(), {"primary": "个人", "secondary": "工作"})

    def test_status_does_not_require_credentials(self):
        with mock.patch.object(duo, "desktop_pids", return_value={"primary": None, "secondary": None}):
            result = duo.status()
        self.assertEqual(len(result), 2)
        self.assertFalse(result[1]["loggedIn"])

    def test_membership_tracks_each_profile_and_survives_cached_refresh(self):
        for home in (duo.PRIMARY, duo.SECONDARY):
            home.mkdir(parents=True)
            (home / "auth.json").write_text("{}")
        responses = [
            {"rateLimits": {"planType": "free"}, "rateLimitsByLimitId": {"codex": {"planType": "plus"}}},
            {"rateLimits": {"planType": "pro"}},
        ]
        with mock.patch.object(duo, "desktop_pids", return_value={"primary": None, "secondary": None}), \
                mock.patch.object(duo, "read_limits", side_effect=responses):
            self.assertEqual([a["planType"] for a in duo.status(refresh=True)], ["plus", "pro"])
            self.assertEqual([a["planType"] for a in duo.status()], ["plus", "pro"])
            (duo.SECONDARY / "auth.json").unlink()
            self.assertIsNone(duo.status()[1]["planType"])

    def test_existing_window_does_not_relaunch(self):
        with mock.patch.object(duo, "desktop_pids", return_value={"primary": 123, "secondary": 456}):
            with mock.patch.object(duo, "app_bundle") as bundle, \
                    mock.patch.object(duo, "desktop_home", return_value=duo.SECONDARY.resolve()):
                self.assertEqual(duo.open_profile("secondary"), 456)
                bundle.assert_not_called()

    def test_default_primary_launch_never_inherits_secondary_home(self):
        default_home = pathlib.Path.home() / ".codex"
        with mock.patch.object(duo, "PRIMARY", default_home), \
                mock.patch.dict(duo.os.environ, {"CODEX_HOME": str(duo.SECONDARY)}), \
                mock.patch.object(duo, "desktop_pids", side_effect=[{"primary": None}, {"primary": 42}]), \
                mock.patch.object(duo, "desktop_home", return_value=default_home.resolve()), \
                mock.patch.object(duo, "app_bundle", return_value=pathlib.Path("/Applications/Codex.app")), \
                mock.patch.object(duo.subprocess, "run", return_value=mock.Mock(returncode=0)) as launch:
            self.assertEqual(duo.open_profile("primary"), 42)
        self.assertIn("CODEX_HOME=" + str(default_home), launch.call_args.args[0])
        self.assertEqual(launch.call_args.kwargs["env"]["CODEX_HOME"], str(default_home))

    def test_existing_window_with_wrong_home_is_rejected(self):
        with mock.patch.object(duo, "desktop_pids", return_value={"primary": 42}), \
                mock.patch.object(duo, "desktop_home", return_value=duo.SECONDARY.resolve()):
            with self.assertRaises(RuntimeError):
                duo.open_profile("primary")

    def test_home_mismatch_does_not_show_another_accounts_quota(self):
        with mock.patch.object(duo, "desktop_pids", return_value={"primary": 42, "secondary": None}), \
                mock.patch.object(duo, "desktop_home", return_value=duo.SECONDARY.resolve()):
            result = duo.status()
        self.assertIn("目录不匹配", result[0]["error"])
        self.assertIsNone(result[0]["usage"])

    def test_usage_windows(self):
        raw = {"rateLimits": {"primary": {"windowDurationMins": 300, "usedPercent": 20},
                              "secondary": {"windowDurationMins": 10080, "usedPercent": 60}}}
        parsed = duo.summarize(raw)
        self.assertEqual(parsed["primary"]["remaining"], 80)
        self.assertEqual(parsed["secondary"]["label"], "每周")

    def test_refresh_reads_both_logged_in_accounts(self):
        for home in (duo.PRIMARY, duo.SECONDARY):
            home.mkdir(parents=True)
            (home / "auth.json").write_text("{}")
        response = {"rateLimits": {"primary": {"windowDurationMins": 300, "usedPercent": 10}}}
        with mock.patch.object(duo, "desktop_pids", return_value={"primary": None, "secondary": None}):
            with mock.patch.object(duo, "read_limits", return_value=response) as reader:
                result = duo.status(refresh=True)
        self.assertEqual(reader.call_count, 2)
        self.assertEqual([item["usage"]["primary"]["remaining"] for item in result], [90, 90])
        self.assertEqual(len(duo.quota_history("primary")), 1)
        self.assertEqual(len(duo.quota_history("secondary")), 1)

    def test_recent_tasks_are_sorted_and_scoped(self):
        duo.PRIMARY.mkdir(parents=True)
        path = duo.PRIMARY / "session_index.jsonl"
        path.write_text("\n".join([
            json.dumps({"id": "older", "thread_name": "旧任务", "updated_at": "2026-10-01T00:00:00Z"}),
            json.dumps({"id": "newer", "thread_name": "新任务", "updated_at": "2026-10-02T00:00:00Z"}),
        ]))
        self.assertEqual([item["title"] for item in duo.recent_tasks("primary")], ["新任务", "旧任务"])
        self.assertEqual(duo.recent_tasks("secondary"), [])

    def test_handoff_uses_task_directory_and_excludes_internal_messages(self):
        workspace = pathlib.Path(self.temp.name) / "project"
        workspace.mkdir()
        session = duo.PRIMARY / "sessions/2026/10/09/rollout-2026-10-09T10-00-00-task-123.jsonl"
        session.parent.mkdir(parents=True)
        session.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in [
            {"type": "session_meta", "payload": {"cwd": str(workspace)}},
            {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "private instruction"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "实现按钮"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "按钮已经完成"}]}},
        ]))
        (duo.PRIMARY / "session_index.jsonl").write_text(json.dumps({"id": "task-123", "thread_name": "做按钮", "updated_at": "2026-10-09T10:00:00Z"}))
        result = duo.prepare_handoff("primary", "task-123")
        self.assertEqual(result["cwd"], str(workspace))
        self.assertEqual(result["messageCount"], 2)
        self.assertIn("实现按钮", result["content"])
        self.assertNotIn("private instruction", result["content"])
        self.assertEqual(pathlib.Path(result["path"]).stat().st_mode & 0o777, 0o600)

    def test_gateway_onboarding_preserves_other_preferences_and_is_idempotent(self):
        config = pathlib.Path(duo.os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"
        config.parent.mkdir()
        original = {"theme": "dark", "projects": {"test": {"hasTrustDialogAccepted": False}}}
        config.write_text(json.dumps(original))
        duo.ensure_claude_onboarding()
        self.assertEqual(json.loads(config.read_text()), dict(original, hasCompletedOnboarding=True))
        backups = list((duo.ROOT / "backups").glob("*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), original)
        duo.ensure_claude_onboarding()
        self.assertEqual(len(list((duo.ROOT / "backups").glob("*.json"))), 1)

    def test_gateway_onboarding_does_not_overwrite_invalid_config(self):
        config = pathlib.Path(duo.os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"
        config.parent.mkdir()
        config.write_text("invalid")
        with self.assertRaises(ValueError):
            duo.ensure_claude_onboarding()
        self.assertEqual(config.read_text(), "invalid")

    def test_homepage_claude_launch_opens_new_session_with_saved_provider(self):
        workspace = pathlib.Path(self.temp.name) / "project ' with spaces"
        workspace.mkdir()
        claude = pathlib.Path(self.temp.name) / "claude"
        claude.write_text("#!/bin/sh\n")
        duo.save_gateway("https://example.com/v1", "test-model")
        with mock.patch.object(duo.shutil, "which", return_value=str(claude)), \
                mock.patch.object(duo.subprocess, "run", return_value=mock.Mock(returncode=0)) as runner:
            script = pathlib.Path(duo.launch_claude_cli(str(workspace)))
        import shlex
        body = script.read_text()
        self.assertIn("cd " + shlex.quote(str(workspace)), body)
        self.assertIn("ANTHROPIC_BASE_URL=https://example.com", body)
        self.assertEqual(shlex.split(body.splitlines()[-1]), ["exec", str(claude), "--model", "test-model"])
        self.assertIn("security find-generic-password", body)
        self.assertEqual(script.stat().st_mode & 0o777, 0o700)
        runner.assert_called_once_with(["open", "-a", "Terminal", str(script)], capture_output=True, text=True)

    def test_homepage_claude_rejects_missing_project(self):
        with self.assertRaisesRegex(RuntimeError, "工作目录"):
            duo.launch_claude_cli(str(pathlib.Path(self.temp.name) / "missing"))

    def test_handoff_rejects_unlisted_task(self):
        with self.assertRaises(RuntimeError):
            duo.prepare_handoff("primary", "missing")

    def test_launch_uses_original_directory_and_absolute_claude(self):
        workspace = pathlib.Path(self.temp.name) / "project with spaces"
        workspace.mkdir()
        handoffs = duo.ROOT / "handoffs"
        handoffs.mkdir(parents=True)
        packet = handoffs / "primary-task.md"
        packet.write_text(f"工作目录：{workspace}\n")
        claude = pathlib.Path(self.temp.name) / "claude"
        claude.write_text("#!/bin/sh\n")
        with mock.patch.object(duo.shutil, "which", return_value=str(claude)):
            with mock.patch.object(duo.subprocess, "run", return_value=mock.Mock(returncode=0)) as runner:
                script = pathlib.Path(duo.launch_handoff(str(packet), str(workspace)))
        body = script.read_text()
        self.assertIn(f"cd '{workspace}'", body)
        self.assertIn(f"exec {claude}", body)
        self.assertIn("ANTHROPIC_BASE_URL=https://api.do.top", body)
        self.assertIn("ANTHROPIC_MODEL=DeepSeek-V4.1-Flash", body)
        self.assertIn("security find-generic-password", body)
        self.assertNotIn("invalid-test-token", body)
        runner.assert_called_once_with(["open", "-a", "Terminal", str(script)], capture_output=True, text=True)
        self.assertEqual(script.stat().st_mode & 0o777, 0o700)

    def test_claude_handoff_reads_session_and_keeps_working_directory(self):
        workspace = pathlib.Path(self.temp.name) / "claude-project"
        workspace.mkdir()
        session = pathlib.Path(self.temp.name) / "12345678-1234-1234-1234-123456789abc.jsonl"
        session.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in [
            {"type": "user", "cwd": str(workspace), "message": {"role": "user", "content": "实现双向切换"}},
            {"type": "assistant", "cwd": str(workspace), "message": {"role": "assistant", "content": [{"type": "text", "text": "已经完成 Claude 部分"}, {"type": "tool_use", "name": "Bash"}]}},
            {"type": "user", "cwd": str(workspace), "message": {"role": "user", "content": [{"type": "tool_result", "content": "secret output"}]}},
        ]))
        with mock.patch.object(duo, "claude_session_files", return_value=[session]):
            tasks = duo.claude_tasks()
            packet = duo.prepare_claude_handoff(session.stem)
        self.assertEqual(tasks[0]["title"], "实现双向切换")
        self.assertEqual(packet["target"], "codex")
        self.assertEqual(packet["cwd"], str(workspace))
        self.assertEqual(packet["messageCount"], 2)
        self.assertNotIn("secret output", packet["content"])

    def test_codex_launch_selects_isolated_account(self):
        workspace = pathlib.Path(self.temp.name) / "project"
        workspace.mkdir()
        handoffs = duo.ROOT / "handoffs"
        handoffs.mkdir(parents=True)
        packet = handoffs / "claude-task.md"
        packet.write_text("交接")
        duo.SECONDARY.mkdir(parents=True)
        (duo.SECONDARY / "auth.json").write_text("{}")
        binary = pathlib.Path(self.temp.name) / "codex"
        binary.write_text("#!/bin/sh\n")
        with mock.patch.object(duo, "codex_binary", return_value=str(binary)):
            with mock.patch.object(duo.subprocess, "run", return_value=mock.Mock(returncode=0)):
                script = pathlib.Path(duo.launch_codex_handoff(str(packet), str(workspace), "secondary"))
        body = script.read_text()
        self.assertIn("export CODEX_HOME=", body)
        self.assertIn("second-home", body)
        self.assertIn(str(binary), body)

    def test_gateway_settings_validate_and_do_not_store_key(self):
        duo.save_gateway("https://example.com/anthropic/", "DeepSeek-V4.1-Flash")
        self.assertEqual(duo.gateway_settings(), {"url": "https://example.com/anthropic", "model": "DeepSeek-V4.1-Flash"})
        duo.save_gateway("https://api.do.top/v1", "DeepSeek-V4.1-Flash")
        self.assertEqual(duo.gateway_settings()["url"], "https://api.do.top")
        self.assertNotIn("key", (duo.ROOT / "gateway.json").read_text())
        with self.assertRaises(ValueError):
            duo.save_gateway("http://example.com", "DeepSeek-V4.1-Flash")

    def test_shared_project_uses_same_directory_with_separate_accounts(self):
        workspace = pathlib.Path(self.temp.name) / "project with spaces"
        workspace.mkdir()
        workspace = workspace.resolve()
        self.assertEqual(duo.save_shared_project(str(workspace))["path"], str(workspace))
        binary = pathlib.Path(self.temp.name) / "codex"
        binary.write_text("#!/bin/sh\n")
        for home in (duo.PRIMARY, duo.SECONDARY):
            home.mkdir(parents=True, exist_ok=True)
            (home / "auth.json").write_text("{}")
        with mock.patch.object(duo, "codex_binary", return_value=str(binary)):
            with mock.patch.object(duo.subprocess, "run", return_value=mock.Mock(returncode=0)):
                primary = pathlib.Path(duo.launch_shared_project("primary"))
                secondary = pathlib.Path(duo.launch_shared_project("secondary"))
        self.assertIn(f"cd '{workspace}'", primary.read_text())
        self.assertIn(f"cd '{workspace}'", secondary.read_text())
        self.assertIn(f"CODEX_HOME={duo.PRIMARY}", primary.read_text())
        self.assertIn("second-home", secondary.read_text())
        self.assertEqual(primary.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(ValueError):
            duo.save_shared_project(str(workspace / "missing"))

    def test_sync_desktop_projects_adds_missing_roots_once(self):
        project = pathlib.Path(self.temp.name) / "native-project"
        project.mkdir()
        source = [{"name": "原生项目", "roots": [{"path": str(project)}], "metadata": {}}]
        with mock.patch.object(duo, "list_desktop_projects", side_effect=[source, [], source, source]):
            with mock.patch.object(duo, "sync_sidebar_project_state"):
                with mock.patch.object(duo, "project_rpc", return_value=[{"project": {}}]) as rpc:
                    result = duo.sync_desktop_projects()
        self.assertEqual(result["created"], ["原生项目"])
        self.assertEqual(rpc.call_args.args[0], "secondary")
        self.assertEqual(rpc.call_args.args[1][0][0], "project/create")
        self.assertEqual(rpc.call_args.args[1][0][1]["roots"], source[0]["roots"])

    def test_sidebar_sync_connects_native_project_to_desktop_state(self):
        project = pathlib.Path(self.temp.name) / "native-project"
        project.mkdir()
        duo.SECONDARY.mkdir(parents=True)
        state_path = duo.SECONDARY / ".codex-global-state.json"
        state_path.write_text(json.dumps({"local-projects": {}, "unrelated": "preserve"}))
        native = [{"id": "native-id", "name": "原生项目", "roots": [{"path": str(project)}]}]
        self.assertTrue(duo.sync_sidebar_project_state("secondary", native))
        state = json.loads(state_path.read_text())
        self.assertEqual(state["unrelated"], "preserve")
        self.assertEqual(len(state["local-projects"]), 1)
        legacy_id = next(iter(state["local-projects"]))
        host = "local:" + str(duo.SECONDARY)
        self.assertEqual(state["app-server-project-id-by-legacy-project-id-by-host"][host][legacy_id], "native-id")
        self.assertFalse(duo.sync_sidebar_project_state("secondary", native))

    def test_project_window_reloads_after_syncing_closed_profile(self):
        with mock.patch.object(duo, "desktop_pids", side_effect=[{"secondary": 123}, {"secondary": None}]):
            with mock.patch.object(duo.os, "kill") as killed:
                with mock.patch.object(duo, "sync_desktop_projects") as synced:
                    with mock.patch.object(duo, "sync_project_history") as history:
                        with mock.patch.object(duo, "open_profile", return_value=456) as opened:
                            self.assertEqual(duo.restart_profile("secondary"), 456)
        killed.assert_called_once()
        synced.assert_called_once()
        history.assert_not_called()
        opened.assert_called_once_with("secondary")

    def test_project_history_snapshot_preserves_destination_edits(self):
        task_id = "11111111-2222-3333-4444-555555555555"
        project = pathlib.Path(self.temp.name) / "project"
        project.mkdir()
        for home, project_id in ((duo.PRIMARY, "main-project"), (duo.SECONDARY, "other-project")):
            home.mkdir(parents=True)
            state = {"local-projects": {project_id: {"rootPaths": [str(project)]}}}
            if home == duo.PRIMARY:
                state["thread-project-assignments"] = {task_id: {"projectKind": "local", "projectId": project_id}}
            (home / ".codex-global-state.json").write_text(json.dumps(state))
        (duo.PRIMARY / "session_index.jsonl").write_text(json.dumps({"id": task_id, "thread_name": "任务"}) + "\n")
        src = duo.PRIMARY / "sessions/2026/10/09" / f"rollout-{task_id}.jsonl"
        src.parent.mkdir(parents=True)
        src.write_text('{"type":"session_meta"}\n')
        first = duo.sync_project_history("primary", "secondary")
        self.assertEqual(first["copied"], 1)
        self.assertEqual(first["assigned"], 1)
        dest = duo.SECONDARY / src.relative_to(duo.PRIMARY)
        self.assertEqual(dest.read_bytes(), src.read_bytes())
        state = json.loads((duo.SECONDARY / ".codex-global-state.json").read_text())
        self.assertEqual(state["thread-project-assignments"][task_id]["projectId"], "other-project")
        self.assertEqual(duo.sync_project_history("primary", "secondary")["copied"], 0)
        dest.write_text('{"type":"destination_changed"}\n')
        src.write_text('{"type":"source_changed"}\n')
        result = duo.sync_project_history("primary", "secondary")
        self.assertEqual(result["skipped"], 1)
        self.assertIn("destination_changed", dest.read_text())

    def test_native_project_assignment_filters_imported_threads(self):
        duo.SECONDARY.mkdir(parents=True)
        path = duo.SECONDARY / "state_5.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, project_id TEXT)")
            db.executemany("INSERT INTO threads VALUES (?, ?)", [("copied", None), ("owned", "own-project")])
            db.commit()
        with mock.patch.object(duo, "project_rpc", return_value=[{"data": []}]) as rpc:
            changed = duo.attach_native_project_threads("secondary", {"copied": "native-project", "owned": "other-project"})
        self.assertEqual(changed, 1)
        rpc.assert_called_once_with("secondary", [("thread/list", {"limit": 1000})])
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT project_id FROM threads WHERE id = 'copied'").fetchone()[0], "native-project")
            self.assertEqual(db.execute("SELECT project_id FROM threads WHERE id = 'owned'").fetchone()[0], "own-project")
        self.assertTrue(path.with_name(path.name + ".duo-backup").exists())

    def test_imported_name_keeps_a_local_rename(self):
        duo.SECONDARY.mkdir(parents=True)
        path = duo.SECONDARY / "state_5.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT)")
            db.executemany("INSERT INTO threads VALUES (?, ?)", [("copied", None), ("renamed", "我的名字")])
            db.commit()
        with mock.patch.object(duo, "project_rpc", return_value=[{}]) as rpc:
            self.assertEqual(duo.name_imported_threads("secondary", {"copied": "原任务", "renamed": "原任务 2"}), 1)
        rpc.assert_called_once_with("secondary", [("thread/name/set", {"threadId": "copied", "name": "原任务"})])


if __name__ == "__main__":
    unittest.main()
