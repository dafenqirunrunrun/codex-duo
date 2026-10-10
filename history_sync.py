"""One-click synchronization using the original native task identity.

Latest completed conversation timestamps select the winner. Busy windows wait;
idle receivers are refreshed with backups and rebuilt paginated history.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def completed_snapshot(paths):
    """Flatten rollout segments, honoring each continuation's history boundary."""
    segments = []
    for path in paths:
        raw = path.read_bytes()
        raw = raw[:raw.rfind(b"\n") + 1]
        if not raw:
            continue
        first = json.loads(raw.splitlines()[0])
        if first.get("type") != "session_meta":
            raise ValueError("会话文件缺少元信息")
        segments.append((raw, first))
    if not segments:
        return None
    meta = segments[0][1]
    if meta["payload"].get("history_base"):
        raise ValueError("这条分支的前序会话文件尚未齐全")
    records = []
    completed_end = 0
    running = False
    for index, (raw, _) in enumerate(segments):
        if index + 1 < len(segments):
            base = segments[index + 1][1]["payload"].get("history_base") or {}
            offset = base.get("end_byte_offset")
            if not isinstance(offset, int) or not 0 <= offset <= len(raw):
                raise ValueError("无法确认会话分段的连续关系")
            raw = raw[:offset]
            if raw and not raw.endswith(b"\n"):
                raise ValueError("会话分段边界不是完整记录")
        for line in raw.splitlines():
            item = json.loads(line)
            if item.get("type") == "session_meta":
                continue
            records.append(item)
            payload = item.get("payload") or {}
            if item.get("type") == "event_msg" and payload.get("type") == "task_started":
                running = True
            if item.get("type") == "event_msg" and payload.get("type") in ("task_complete", "turn_aborted"):
                completed_end = len(records)
                running = False
    if not completed_end:
        return None
    records = records[:completed_end]
    # Administrative settings and token telemetry do not make a new conversation.
    messages = [item["payload"] for item in records if item.get("type") == "response_item"
                and item.get("payload", {}).get("type") == "message"
                and item["payload"].get("role") in ("user", "assistant")]
    signature = digest(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode())
    stamp = records[-1].get("timestamp")
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        completed_at = int(parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).timestamp() * 1_000_000)
    except (ValueError, TypeError, AttributeError):
        completed_at = 0
    return {"meta": meta, "records": records, "signature": signature,
            "completedAt": completed_at, "running": running,
            "messages": [digest(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()) for item in messages]}


def profile_has_running_task(backend, profile):
    path = backend.profile_home(profile) / "state_5.sqlite"
    if not path.exists():
        return False
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        paths = [Path(row[0]) for row in db.execute("SELECT rollout_path FROM threads WHERE archived = 0")]
    for rollout in paths:
        if not rollout.exists():
            continue
        running = False
        with rollout.open(errors="replace") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get("type") == "event_msg":
                    kind = (item.get("payload") or {}).get("type")
                    if kind == "task_started":
                        running = True
                    elif kind in ("task_complete", "turn_aborted"):
                        running = False
        if running:
            return True
    return False


def encode_snapshot(snapshot, task_id):
    meta = json.loads(json.dumps(snapshot["meta"]))
    payload = meta["payload"]
    payload.update(id=task_id, session_id=task_id, history_mode="paginated")
    for key in ("history_base", "forked_from_id", "forked_from_ordinal_exclusive"):
        payload.pop(key, None)
    return ("\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                      for item in [meta] + snapshot["records"]) + "\n").encode()


def file_in_use(path):
    if not path.exists():
        return False
    result = subprocess.run(["/usr/sbin/lsof", "-t", str(path)], capture_output=True, text=True, timeout=5)
    if result.returncode not in (0, 1):
        raise RuntimeError("暂时无法检查对话是否正在使用")
    return bool(result.stdout.strip())


def project_tasks(backend, profile, generated_ids):
    home = backend.profile_home(profile)
    state_path = home / ".codex-global-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    local = state.get("local-projects") or {}
    mappings = ((state.get("app-server-project-id-by-legacy-project-id-by-host") or {}).get("local:" + str(home)) or {})
    by_native = {native: local.get(legacy, {}) for legacy, native in mappings.items()}
    assignments = state.get("thread-project-assignments") or {}
    db_path = home / "state_5.sqlite"
    if not db_path.exists():
        return {}
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT id, name, title, project_id, cwd FROM threads WHERE archived = 0").fetchall()
    tasks = {}
    for task_id, name, title, native_id, cwd in rows:
        if task_id in generated_ids:
            continue
        project = local.get((assignments.get(task_id) or {}).get("projectId")) or by_native.get(native_id)
        if not project:
            project = next((item for item in local.values() if cwd in (item.get("rootPaths") or [])), None)
        if not project or not project.get("rootPaths"):
            continue
        tasks[task_id] = {"id": task_id, "title": name or title or "未命名对话", "roots": project["rootPaths"]}
    return tasks


def install_snapshot(backend, target, task, snapshot, entry, source_label, original=False):
    home = backend.profile_home(target)
    task_id = entry["id"]
    path = home / entry["path"]
    if path.exists():
        # Resuming can create another rollout segment while leaving this file intact.
        # Such a local continuation owns its history and must never be reprojected.
        local_paths = backend.session_files(target, task_id)
        if len(local_paths) != 1 or local_paths[0].resolve() != path.resolve():
            return "edited"
        if digest(path.read_bytes()) != entry.get("fileDigest"):
            return "edited"
        if file_in_use(path):
            return "open"
    raw = encode_snapshot(snapshot, task_id)
    # Retain the prior copy if projection/indexing fails during the update.
    backup_path = backend.ROOT / "history-backups" / target / (task_id + ".jsonl")
    if path.exists():
        backend.atomic_write(backup_path, path.read_bytes())
    backend.atomic_write(path, raw)
    entry["fileDigest"] = digest(raw)
    state_path = home / ".codex-global-state.json"
    state = json.loads(state_path.read_text())
    legacy_id = next(key for key, project in state["local-projects"].items() if project.get("rootPaths") == task["roots"])
    native_id = state["app-server-project-id-by-legacy-project-id-by-host"]["local:" + str(home)][legacy_id]
    # Rebuild only the generated, unopened snapshot's paginated projection.
    history_db = home / "thread_history_1.sqlite"
    if history_db.exists():
        with closing(sqlite3.connect(history_db, timeout=10)) as db:
            with db:
                for table in ("thread_turns", "thread_items", "thread_realtime_items", "thread_history_projection_state"):
                    db.execute(f"DELETE FROM {table} WHERE thread_id = ?", (task_id,))
    response = backend.project_rpc(target, [("thread/list", {"limit": 1000}),
                                            ("thread/resume", {"threadId": task_id, "excludeTurns": True}),
                                            ("thread/turns/list", {"threadId": task_id, "limit": 1})])
    if not response[-1].get("data"):
        raise RuntimeError("导入后的正文校验失败，没有标记为同步完成")
    backend.attach_native_project_threads(target, {task_id: native_id})
    title = task['title'][:200] if original else f"{task['title'][:130]} · 来自{source_label}"
    backend.project_rpc(target, [("thread/name/set", {"threadId": task_id, "name": title})])
    # Re-read immediately before merging sidebar state, preserving unrelated changes.
    state = json.loads(state_path.read_text())
    state.setdefault("thread-project-assignments", {})[task_id] = {"projectKind": "local", "projectId": legacy_id}
    state.setdefault("thread-project-membership-host-ids", {})[task_id] = "local"
    backend.atomic_write(state_path, json.dumps(state, ensure_ascii=False).encode())
    entry["signature"] = snapshot["signature"]
    entry["fileDigest"] = digest(path.read_bytes())
    entry["updatedAt"] = int(time.time())
    return "updated"


def install_original(backend, target, task, snapshot):
    """Replace a closed receiver's task in place, with a restorable backup."""
    if backend.desktop_pids().get(target):
        raise RuntimeError("窗口刷新尚未完成，暂未覆盖对话，请稍后重试同步")
    home = backend.profile_home(target)
    task_id = task["id"]
    try:
        old_paths = backend.session_files(target, task_id)
    except RuntimeError:
        old_paths = []
    if any(file_in_use(path) for path in old_paths):
        raise RuntimeError("接收任务仍被后台进程使用，暂未覆盖")
    backup = backend.ROOT / "history-backups" / target / f"{task_id}-{time.time_ns()}"
    backend.private_dir(backup)
    saved_dbs = []
    for name in ("state_5.sqlite", "thread_history_1.sqlite"):
        db_path = home / name
        if db_path.exists():
            with closing(sqlite3.connect(db_path, timeout=10)) as source:
                with closing(sqlite3.connect(backup / name)) as dest:
                    source.backup(dest)
            os.chmod(backup / name, 0o600)
            saved_dbs.append(name)
    state_path = home / ".codex-global-state.json"
    state_raw = state_path.read_bytes()
    backend.atomic_write(backup / state_path.name, state_raw)
    entry = {"id": task_id, "path": f"sessions/{time.strftime('%Y/%m/%d')}/rollout-{time.strftime('%Y-%m-%dT%H-%M-%S')}-{task_id}.jsonl"}
    new_path = home / entry["path"]
    moved = []
    try:
        for path in old_paths:
            dest = backup / "rollouts" / path.relative_to(home)
            backend.private_dir(dest.parent)
            os.replace(path, dest)
            moved.append((path, dest))
        state_db = home / "state_5.sqlite"
        if state_db.exists():
            with closing(sqlite3.connect(state_db, timeout=10)) as db:
                with db:
                    db.execute("UPDATE threads SET rollout_path = ?, history_mode = 'paginated' WHERE id = ?",
                               (str(new_path), task_id))
        install_snapshot(backend, target, task, snapshot, entry, "", original=True)
        return str(backup)
    except Exception:
        if new_path.exists():
            new_path.unlink()
        for path, saved in moved:
            backend.private_dir(path.parent)
            os.replace(saved, path)
        for name in saved_dbs:
            with closing(sqlite3.connect(backup / name)) as source:
                with closing(sqlite3.connect(home / name, timeout=10)) as dest:
                    source.row_factory = sqlite3.Row
                    tables = ["threads"] if name == "state_5.sqlite" else ["thread_turns", "thread_items", "thread_realtime_items", "thread_history_projection_state"]
                    with dest:
                        for table in tables:
                            key = "id" if table == "threads" else "thread_id"
                            rows = source.execute(f"SELECT * FROM {table} WHERE {key} = ?", (task_id,)).fetchall()
                            dest.execute(f"DELETE FROM {table} WHERE {key} = ?", (task_id,))
                            if rows:
                                columns = list(rows[0].keys())
                                dest.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [tuple(row) for row in rows])
        backend.atomic_write(state_path, state_raw)
        raise


def archive_legacy_copy(backend, source, target, task_id, copies):
    entry = copies.get(f"{source}:{target}:{task_id}")
    if not entry or entry.get("archived"):
        return
    path = backend.profile_home(target) / entry["path"]
    if path.exists() and digest(path.read_bytes()) == entry.get("fileDigest"):
        paths = backend.session_files(target, entry["id"])
        if len(paths) == 1 and not file_in_use(path):
            backend.project_rpc(target, [("thread/archive", {"threadId": entry["id"]})])
            entry["archived"] = True


def latest_key(snapshot):
    # Content timestamps survive import; filesystem and DB modification times do not.
    # Exact timestamp ties converge deterministically without alternating accounts.
    return (snapshot.get("completedAt", 0), len(snapshot["messages"]), snapshot["signature"])


def synchronize(backend, receiver=None, auto_refresh=False):
    """Latest completed content wins per native task ID; refresh receivers once."""
    manifest_path = backend.ROOT / "conversation-sync.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"copies": {}}
    copies = manifest["copies"]
    generated = {entry["id"] for entry in copies.values()}
    tasks = {profile: project_tasks(backend, profile, generated) for profile in ("primary", "secondary")}
    snapshots = {}
    errors = []
    for profile, items in tasks.items():
        for task_id in list(items):
            try:
                snapshot = completed_snapshot(backend.session_files(profile, task_id))
                snapshots[profile, task_id] = snapshot
            except (ValueError, RuntimeError, OSError) as exc:
                errors.append({"title": items[task_id]["title"][:100], "reason": str(exc)})
    results = {"updated": 0, "unchanged": 0, "pending": [], "errors": errors, "copies": [], "reloadProfiles": []}
    labels = backend.settings()
    pids = backend.desktop_pids()
    active = [profile for profile in ("primary", "secondary")
              if pids.get(profile) and profile_has_running_task(backend, profile)]
    if active:
        message = "正在回复，结束后自动同步" if auto_refresh else "正在回复；点击同步可预约完成后自动同步"
        results["pending"].append({"title": "等待当前任务完成", "reason": "、".join(labels[p] for p in active) + message})
        return results
    plans = {"primary": [], "secondary": []}
    for task_id in sorted(set(tasks["primary"]) | set(tasks["secondary"])):
        left, right = snapshots.get(("primary", task_id)), snapshots.get(("secondary", task_id))
        # A read failure on either side must not be treated as an absent task.
        if any(task_id in tasks[p] and (p, task_id) not in snapshots for p in tasks):
            continue
        if not left and not right:
            continue
        if left and right and left["signature"] == right["signature"]:
            results["unchanged"] += 1
            continue
        source = "primary" if left and (not right or latest_key(left) > latest_key(right)) else "secondary"
        target = "secondary" if source == "primary" else "primary"
        if receiver and receiver != target:
            continue
        plans[target].append((source, task_id, tasks[source][task_id], snapshots[source, task_id]))
    for target, planned in plans.items():
        if not planned:
            continue
        was_running = bool(backend.desktop_pids().get(target))
        if was_running and not auto_refresh:
            results["pending"].append({"title": labels[target], "target": target, "reason": "有新进度，点击同步会话与项目即可更新"})
            continue
        try:
            if was_running:
                # Recheck directly before refreshing to avoid interrupting a new turn.
                if profile_has_running_task(backend, target):
                    results["pending"].append({"title": labels[target], "reason": "正在回复，结束后自动同步"})
                    continue
                backend.stop_profile(target)
            for source, task_id, task, snapshot in planned:
                try:
                    install_original(backend, target, task, snapshot)
                    archive_legacy_copy(backend, source, target, task_id, copies)
                    results["updated"] += 1
                    results["copies"].append({"title": task["title"][:100], "source": source, "target": target, "id": task_id})
                except Exception as exc:
                    results["errors"].append({"title": task["title"][:100], "reason": str(exc)})
                finally:
                    backend.atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False).encode())
        except Exception as exc:
            results["errors"].append({"title": labels[target], "reason": str(exc)})
        finally:
            if was_running and backend.desktop_pids().get(target) is None:
                try:
                    backend.open_profile(target)
                    results["reloadProfiles"].append(target)
                except Exception as exc:
                    results["errors"].append({"title": labels[target], "reason": "重新打开失败：" + str(exc)})
    return results
