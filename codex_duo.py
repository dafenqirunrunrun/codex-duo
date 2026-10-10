#!/usr/bin/env python3
"""Two isolated Codex desktop windows and their usage, without token swapping."""

import argparse
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import sqlite3
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone

ROOT = Path(os.environ.get("CODEX_DUO_HOME", Path.home() / "Library/Application Support/Codex Duo"))
PRIMARY = Path(os.environ.get("CODEX_DUO_CODEX_HOME", Path.home() / ".codex"))
SECONDARY = ROOT / "second-home"
SECONDARY_DATA = ROOT / "second-desktop-data"
GATEWAY_KEYCHAIN_SERVICE = "local.codex.duo.gateway"


def private_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)


def atomic_write(path, raw):
    private_dir(path.parent)
    fd, name = tempfile.mkstemp(prefix=".duo-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def settings():
    path = ROOT / "settings.json"
    if not path.exists():
        return {"primary": "账号 1", "secondary": "账号 2"}
    obj = json.loads(path.read_text())
    return {key: str(obj.get(key) or label) for key, label in (("primary", "账号 1"), ("secondary", "账号 2"))}


def rename(profile, name):
    name = name.strip()
    if not 1 <= len(name) <= 30:
        raise ValueError("名称应为 1 到 30 个字符")
    obj = settings()
    obj[profile] = name
    atomic_write(ROOT / "settings.json", json.dumps(obj, ensure_ascii=False).encode())


def gateway_settings():
    path = ROOT / "gateway.json"
    default = {"url": "https://api.do.top", "model": "DeepSeek-V4.1-Flash"}
    if not path.exists():
        return default
    obj = json.loads(path.read_text())
    url = str(obj.get("url") or default["url"]).rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return {"url": url,
            "model": str(obj.get("model") or default["model"])}


def save_gateway(url, model):
    from urllib.parse import urlparse
    url = url.strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("接口地址必须是有效的 HTTPS 地址")
    if not re.fullmatch(r"[A-Za-z0-9._:/-]{2,100}", model):
        raise ValueError("模型名称格式无效")
    atomic_write(ROOT / "gateway.json", json.dumps({"url": url, "model": model}).encode())


def app_bundle():
    candidates = [Path("/Applications/ChatGPT.app"), Path.home() / "Applications/ChatGPT.app",
                  Path("/Applications/Codex.app"), Path.home() / "Applications/Codex.app"]
    found = subprocess.run(["mdfind", "kMDItemCFBundleIdentifier == 'com.openai.codex'"], capture_output=True, text=True)
    candidates += [Path(line) for line in found.stdout.splitlines()]
    return next((p for p in candidates if (p / "Contents/Resources/codex").is_file()), None)


def codex_binary():
    direct = os.environ.get("CODEX_DUO_CODEX_BIN") or shutil.which("codex")
    if direct:
        return direct
    app = app_bundle()
    return str(app / "Contents/Resources/codex") if app else None


def profile_home(profile):
    return PRIMARY if profile == "primary" else SECONDARY


def shared_project():
    path = ROOT / "shared-project.json"
    if not path.exists():
        return {"path": ""}
    return {"path": str(json.loads(path.read_text()).get("path") or "")}


def save_shared_project(path_text):
    path = Path(path_text).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("请选择已存在的项目文件夹")
    atomic_write(ROOT / "shared-project.json", json.dumps({"path": str(path)}, ensure_ascii=False).encode())
    return shared_project()


def launch_shared_project(profile):
    if profile not in ("primary", "secondary"):
        raise ValueError("账号无效")
    cwd = Path(shared_project()["path"])
    if not cwd.is_dir() or not shared_project()["path"]:
        raise RuntimeError("请先选择共享项目文件夹")
    binary = codex_binary()
    if not binary or not Path(binary).is_file():
        raise RuntimeError("找不到 Codex CLI")
    home = profile_home(profile)
    if not (home / "auth.json").is_file():
        raise RuntimeError("所选 Codex 账号尚未登录")
    script = ROOT / "handoffs" / f"shared-{profile}-{int(time.time() * 1000)}.command"
    body = (f"#!/bin/zsh\ncd {shlex.quote(str(cwd))} || exit 1\n"
            f"export CODEX_HOME={shlex.quote(str(home))}\n"
            f"exec {shlex.quote(binary)}\n")
    atomic_write(script, body.encode("utf-8"))
    os.chmod(script, 0o700)
    result = subprocess.run(["open", "-a", "Terminal", str(script)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("启动终端失败：" + result.stderr.strip())
    return str(script)


def desktop_pids():
    result = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=True)
    found = {"primary": None, "secondary": None}
    marker = "--user-data-dir=" + str(SECONDARY_DATA)
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        command = fields[1]
        executable = command.split(" --", 1)[0]
        if executable.endswith(("/Contents/MacOS/ChatGPT", "/Contents/MacOS/Codex")):
            found["secondary" if marker in command else "primary"] = int(fields[0])
    return found


def desktop_home(pid):
    """Read only the selected home from process environment; never expose tokens."""
    result = subprocess.run(["ps", "eww", "-p", str(pid), "-o", "command="],
                            capture_output=True, text=True)
    if result.returncode or not result.stdout.strip():
        return None
    match = re.search(r"(?:^| )CODEX_HOME=(.*?)(?= [A-Za-z_][A-Za-z0-9_]*=|$)", result.stdout)
    return Path(match.group(1)).expanduser().resolve() if match else (Path.home() / ".codex").resolve()


def open_profile(profile):
    pid = desktop_pids()[profile]
    if pid:
        if desktop_home(pid) != profile_home(profile).resolve():
            raise RuntimeError("该窗口使用了其他账号目录，请通过刷新窗口修复")
        return pid
    app = app_bundle()
    if not app:
        raise RuntimeError("找不到 Codex 桌面 App。请先将它放入“应用程序”文件夹。")
    home = profile_home(profile)
    # Always pin both profiles. Default home must not inherit the launching
    # Codex process's CODEX_HOME when Duo runs inside account 2.
    command = ["open", "-n", str(app), "--env", "CODEX_HOME=" + str(home)]
    if profile == "secondary":
        private_dir(SECONDARY)
        private_dir(SECONDARY_DATA)
        command += ["--args", "--user-data-dir=" + str(SECONDARY_DATA)]
    result = subprocess.run(command, capture_output=True, text=True, env=dict(os.environ, CODEX_HOME=str(home)))
    if result.returncode:
        raise RuntimeError("启动 Codex 失败：" + result.stderr.strip())
    for _ in range(40):
        pid = desktop_pids()[profile]
        if pid:
            if desktop_home(pid) != home.resolve():
                raise RuntimeError("窗口已启动，但账号目录校验未通过，请勿在该窗口继续任务")
            return pid
        time.sleep(0.25)
    raise RuntimeError("Codex 已启动，但暂时无法找到窗口")


def stop_profile(profile):
    pid = desktop_pids()[profile]
    if pid:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if desktop_pids()[profile] is None:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Codex 窗口未能退出，项目布局未重载")


def restart_profile(profile):
    stop_profile(profile)
    sync_desktop_projects()
    return open_profile(profile)


def rpc_lines(process, deadline):
    while time.monotonic() < deadline:
        buffer = getattr(process, "_duo_rpc_buffer", b"")
        if b"\n" in buffer:
            line, process._duo_rpc_buffer = buffer.split(b"\n", 1)
            try:
                yield json.loads(line)
            except (ValueError, UnicodeDecodeError):
                pass
            continue
        ready, _, _ = select.select([process.stdout], [], [], min(0.2, max(0, deadline - time.monotonic())))
        if ready:
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            process._duo_rpc_buffer = buffer + chunk


def read_limits(profile):
    binary = codex_binary()
    home = profile_home(profile)
    if not binary:
        raise RuntimeError("找不到 Codex 运行程序")
    if not (home / "auth.json").exists():
        raise RuntimeError("尚未登录")
    proc = subprocess.Popen([binary, "app-server", "--listen", "stdio://"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            env=dict(os.environ, CODEX_HOME=str(home)), bufsize=1)
    try:
        def send(item):
            proc.stdin.write(json.dumps(item) + "\n")
            proc.stdin.flush()
        deadline = time.monotonic() + 15
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "codex_duo", "title": "Codex Duo", "version": "0.2"}}})
        for item in rpc_lines(proc, deadline):
            if item.get("id") == 1:
                if "error" in item:
                    raise RuntimeError(str(item["error"]))
                break
        else:
            raise RuntimeError("连接 Codex 超时")
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "account/rateLimits/read", "params": {}})
        for item in rpc_lines(proc, deadline):
            if item.get("id") == 2:
                if "error" in item:
                    raise RuntimeError(str(item["error"]))
                return item.get("result", {})
        raise RuntimeError("读取额度超时")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def project_rpc(profile, requests):
    binary = codex_binary()
    if not binary:
        raise RuntimeError("找不到 Codex 运行程序")
    proc = subprocess.Popen([binary, "app-server", "--listen", "stdio://"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            env=dict(os.environ, CODEX_HOME=str(profile_home(profile))), bufsize=1)
    try:
        def call(identifier, method, params):
            proc.stdin.write(json.dumps({"id": identifier, "method": method, "params": params}) + "\n")
            proc.stdin.flush()
            for item in rpc_lines(proc, time.monotonic() + 12):
                if item.get("id") == identifier:
                    if item.get("error"):
                        raise RuntimeError(f"{method}: {item['error']}")
                    return item.get("result") or {}
            raise RuntimeError(f"{method} 超时")
        call(1, "initialize", {"clientInfo": {"name": "codex_duo", "title": "Codex Duo", "version": "0.3"},
                               "capabilities": {"experimentalApi": True}})
        proc.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n")
        proc.stdin.flush()
        return [call(i + 2, method, params) for i, (method, params) in enumerate(requests)]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def list_desktop_projects(profile):
    result = project_rpc(profile, [("project/list", {"limit": 100})])[0]
    return result.get("data") or []


def sync_desktop_projects():
    projects = {profile: list_desktop_projects(profile) for profile in ("primary", "secondary")}
    existing = {profile: {tuple(root.get("path") for root in project.get("roots", []))
                          for project in items} for profile, items in projects.items()}
    created = []
    for source_profile, target_profile in (("primary", "secondary"), ("secondary", "primary")):
        for project in projects[source_profile]:
            roots = project.get("roots") or []
            paths = tuple(root.get("path") for root in roots)
            if not paths or paths in existing[target_profile] or not all(path and Path(path).is_dir() for path in paths):
                continue
            params = {"name": project.get("name") or Path(paths[0]).name,
                      "roots": roots, "metadata": project.get("metadata") or {},
                      "idempotencyKey": str(uuid.uuid5(uuid.NAMESPACE_URL, "codex-duo:" + "|".join(paths)))}
            project_rpc(target_profile, [("project/create", params)])
            existing[target_profile].add(paths)
            created.append(params["name"])
    for profile in ("primary", "secondary"):
        sync_sidebar_project_state(profile, list_desktop_projects(profile))
    return {"created": created, "total": len(existing["primary"] | existing["secondary"])}


def sync_desktop_workspace(refresh_windows=False):
    from history_sync import synchronize
    private_dir(ROOT)
    with (ROOT / "workspace-sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("另一次同步正在进行，请稍后再试")
        queued = ROOT / "pending-workspace-sync.json"
        requested = refresh_windows or queued.exists()
        if refresh_windows:
            atomic_write(queued, json.dumps({"requestedAt": int(time.time())}).encode())
        result = sync_desktop_projects()
        result["history"] = synchronize(sys.modules[__name__], auto_refresh=requested)
        if requested and not result["history"].get("pending") and queued.exists():
            queued.unlink()
        atomic_write(ROOT / "last-workspace-sync.json", json.dumps(result, ensure_ascii=False).encode())
        return result


def sync_sidebar_project_state(profile, native_projects):
    home = profile_home(profile)
    path = home / ".codex-global-state.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    local = dict(state.get("local-projects") or {})
    order = list(state.get("project-order") or [])
    host = "local:" + str(home)
    maps = dict(state.get("app-server-project-id-by-legacy-project-id-by-host") or {})
    mapping = dict(maps.get(host) or {})
    changed = False
    for project in native_projects:
        roots = [root.get("path") for root in project.get("roots") or []]
        if not roots or not all(roots):
            continue
        legacy_id = next((identifier for identifier, item in local.items()
                          if item.get("rootPaths") == roots), None)
        if not legacy_id:
            legacy_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "codex-duo:sidebar:" + host + ":" + "|".join(roots)))
            now = int(time.time() * 1000)
            local[legacy_id] = {"id": legacy_id, "name": project.get("name") or Path(roots[0]).name,
                                "rootPaths": roots, "createdAt": now, "updatedAt": now}
            changed = True
        if legacy_id not in order:
            order.append(legacy_id)
            changed = True
        if mapping.get(legacy_id) != project.get("id"):
            mapping[legacy_id] = project["id"]
            changed = True
    if changed:
        state["local-projects"] = local
        state["project-order"] = order
        maps[host] = mapping
        state["app-server-project-id-by-legacy-project-id-by-host"] = maps
        if path.exists():
            backup = path.with_name(path.name + ".duo-backup")
            if not backup.exists():
                shutil.copy2(path, backup)
        atomic_write(path, json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode())
    return changed


def sync_project_history(source_profile, target_profile):
    """Copy local project task snapshots into a closed account's desktop sidebar.

    A destination task is refreshed only while its file still matches our last
    copy, so continuing it in the other account never gets overwritten.
    """
    source_home, target_home = profile_home(source_profile), profile_home(target_profile)
    source_state_path = source_home / ".codex-global-state.json"
    target_state_path = target_home / ".codex-global-state.json"
    if not source_state_path.exists() or not target_state_path.exists():
        return {"copied": 0, "assigned": 0, "skipped": 0}
    source = json.loads(source_state_path.read_text())
    target = json.loads(target_state_path.read_text())
    source_projects = source.get("local-projects") or {}
    target_projects = target.get("local-projects") or {}
    target_by_roots = {tuple(item.get("rootPaths") or []): key for key, item in target_projects.items()}
    host = "local:" + str(target_home)
    native_ids = ((target.get("app-server-project-id-by-legacy-project-id-by-host") or {}).get(host) or {})
    source_assignments = source.get("thread-project-assignments") or {}
    target_assignments = dict(target.get("thread-project-assignments") or {})
    membership = dict(target.get("thread-project-membership-host-ids") or {})
    source_index = source_home / "session_index.jsonl"
    target_index = target_home / "session_index.jsonl"
    source_entries = {}
    if source_index.exists():
        for line in source_index.read_text(errors="replace").splitlines():
            try:
                item = json.loads(line)
                if item.get("id"):
                    source_entries[item["id"]] = line
            except ValueError:
                continue
    known_index_ids = set()
    if target_index.exists():
        for line in target_index.read_text(errors="replace").splitlines():
            try:
                known_index_ids.add(json.loads(line).get("id"))
            except ValueError:
                continue
    manifest_path = ROOT / "history-mirror.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    copied = assigned = skipped = 0
    index_additions = []
    native_assignments = {}
    names = {}
    for task_id, assignment in source_assignments.items():
        if assignment.get("projectKind") != "local" or task_id not in source_entries:
            continue
        source_project = source_projects.get(assignment.get("projectId")) or {}
        target_project_id = target_by_roots.get(tuple(source_project.get("rootPaths") or []))
        if not target_project_id:
            continue
        try:
            paths = session_files(source_profile, task_id)
        except RuntimeError:
            continue
        safe = True
        pending = []
        for src in paths:
            rel = src.relative_to(source_home)
            dest = target_home / rel
            raw = src.read_bytes()
            if raw and not raw.endswith(b"\n"):
                raw = raw[:raw.rfind(b"\n") + 1]
            source_digest = hashlib.sha256(raw).hexdigest()
            key = f"{source_profile}:{target_profile}:{rel}"
            previous = manifest.get(key)
            if dest.exists():
                current_digest = hashlib.sha256(dest.read_bytes()).hexdigest()
                if current_digest != source_digest and current_digest != previous:
                    safe = False
                    break
            pending.append((dest, raw, source_digest, key))
        if not safe:
            skipped += 1
            continue
        if native_ids.get(target_project_id):
            native_assignments[task_id] = native_ids[target_project_id]
            names[task_id] = str(json.loads(source_entries[task_id]).get("thread_name") or "").strip()
        for dest, raw, digest, key in pending:
            if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
                atomic_write(dest, raw)
                copied += 1
            manifest[key] = digest
        if task_id not in known_index_ids:
            index_additions.append(source_entries[task_id])
            known_index_ids.add(task_id)
        if target_assignments.get(task_id) != {"projectKind": "local", "projectId": target_project_id}:
            target_assignments[task_id] = {"projectKind": "local", "projectId": target_project_id}
            membership[task_id] = "local"
            assigned += 1
    if index_additions:
        old = target_index.read_bytes() if target_index.exists() else b""
        atomic_write(target_index, old.rstrip(b"\n") + (b"\n" if old else b"") +
                     ("\n".join(index_additions) + "\n").encode())
    if assigned:
        target["thread-project-assignments"] = target_assignments
        target["thread-project-membership-host-ids"] = membership
        backup = target_state_path.with_name(target_state_path.name + ".duo-backup")
        if not backup.exists():
            shutil.copy2(target_state_path, backup)
        atomic_write(target_state_path, json.dumps(target, ensure_ascii=False, separators=(",", ":")).encode())
    if copied or assigned:
        atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False).encode())
    native_assigned = attach_native_project_threads(target_profile, native_assignments)
    name_imported_threads(target_profile, names)
    return {"copied": copied, "assigned": assigned, "nativeAssigned": native_assigned, "skipped": skipped}


def attach_native_project_threads(profile, assignments):
    if not assignments:
        return 0
    # The app-server indexes newly copied rollouts when it lists local threads.
    project_rpc(profile, [("thread/list", {"limit": 1000})])
    path = profile_home(profile) / "state_5.sqlite"
    if not path.exists():
        return 0
    with closing(sqlite3.connect(path, timeout=10)) as db:
        backup = path.with_name(path.name + ".duo-backup")
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as snapshot:
                db.backup(snapshot)
            os.chmod(backup, 0o600)
        changed = 0
        for task_id, project_id in assignments.items():
            cursor = db.execute("UPDATE threads SET project_id = ? WHERE id = ? AND (project_id IS NULL OR project_id = '')",
                                (project_id, task_id))
            changed += cursor.rowcount
        db.commit()
        return changed


def name_imported_threads(profile, names):
    path = profile_home(profile) / "state_5.sqlite"
    if not names or not path.exists():
        return 0
    requests = []
    with closing(sqlite3.connect(path)) as db:
        for task_id, name in names.items():
            if not name:
                continue
            row = db.execute("SELECT name FROM threads WHERE id = ?", (task_id,)).fetchone()
            if row and not row[0]:
                requests.append(("thread/name/set", {"threadId": task_id, "name": name[:200]}))
    if requests:
        project_rpc(profile, requests)
    return len(requests)


def desktop_project_overview():
    projects = {profile: list_desktop_projects(profile) for profile in ("primary", "secondary")}
    grouped = {}
    for profile, items in projects.items():
        for project in items:
            roots = project.get("roots") or []
            paths = tuple(root.get("path") for root in roots)
            if not paths:
                continue
            entry = grouped.setdefault(paths, {"name": project.get("name") or "未命名项目",
                                               "path": paths[0] or "", "profiles": []})
            entry["profiles"].append(profile)
    return [{"name": entry["name"], "path": entry["path"],
             "synced": len(entry["profiles"]) == 2} for entry in grouped.values()]


def summarize(result):
    limits = (result.get("rateLimitsByLimitId") or {}).get("codex") or result.get("rateLimits") or {}
    output = {}
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if isinstance(window, dict):
            mins = window.get("windowDurationMins")
            label = "5 小时" if mins == 300 else "每周" if mins == 10080 else f"{mins} 分钟" if mins else key
            output[key] = {"label": label, "remaining": max(0, 100 - int(window["usedPercent"])), "resetsAt": window.get("resetsAt")}
    return output


def recent_tasks(profile, count=50):
    index = profile_home(profile) / "session_index.jsonl"
    if not index.exists():
        return []
    latest = {}
    with index.open(errors="replace") as stream:
        for line in stream:
            try:
                item = json.loads(line)
                identifier = item.get("id")
                if identifier and item.get("thread_name"):
                    if identifier not in latest or str(item.get("updated_at", "")) > str(latest[identifier].get("updated_at", "")):
                        latest[identifier] = item
            except (ValueError, TypeError):
                continue
    ordered = sorted(latest.values(), key=lambda item: str(item.get("updated_at", "")), reverse=True)
    return [{"id": str(item["id"]),
             "title": " ".join(str(item["thread_name"]).split())[:100],
             "updatedAt": str(item.get("updated_at", ""))}
            for item in ordered[:count]]


def session_files(profile, task_id):
    if not task_id or not all(char.isalnum() or char == "-" for char in task_id):
        raise ValueError("任务 ID 无效")
    base = profile_home(profile) / "sessions"
    if not base.exists():
        raise RuntimeError("找不到这条任务的本地会话记录")
    pattern = re.compile(r"-" + re.escape(task_id) + r"(?:_[0-9a-f-]+)?\.jsonl$")
    files = [path for path in base.rglob(f"*{task_id}*.jsonl") if pattern.search(path.name)]
    if not files:
        raise RuntimeError("找不到这条任务的本地会话记录")
    return sorted(files, key=lambda path: path.name)


def session_context(paths):
    cwd = None
    messages = []
    for path in paths:
        with path.open(errors="replace") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                payload = item.get("payload") or {}
                if item.get("type") == "session_meta":
                    cwd = payload.get("cwd") or cwd
                if item.get("type") != "response_item" or payload.get("type") != "message":
                    continue
                role = payload.get("role")
                if role not in ("user", "assistant"):
                    continue
                parts = []
                for content in payload.get("content") or []:
                    value = content.get("text", "")
                    if content.get("type") not in ("input_text", "output_text") or not value.strip():
                        continue
                    if value.lstrip().startswith(("<environment_context>", "<recommended_plugins>", "<app-context>")):
                        continue
                    parts.append(value.strip())
                if parts:
                    message = (role, "\n\n".join(parts))
                    messages.append(message)
    if not cwd or not Path(cwd).is_dir():
        raise RuntimeError("任务的原工作目录已不存在，无法安全交接")
    return Path(cwd), messages


def claude_session_files(count=8):
    base = Path.home() / ".claude/projects"
    if not base.is_dir():
        return []
    files = (path for path in base.glob("*/*.jsonl") if re.fullmatch(r"[0-9a-f-]{36}\.jsonl", path.name))
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[:max(30, count * 5)]


def claude_usage():
    from claude_usage import local_usage
    cache_path = ROOT / "claude-usage-cache.json"
    try:
        cache = json.loads(cache_path.read_text())
        if cache.get("version") != 1:
            cache = {}
    except (OSError, ValueError, AttributeError):
        cache = {}
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
    result, cache = local_usage(base, cache)
    atomic_write(cache_path, json.dumps(cache).encode())
    return result


def claude_context(path, first_only=False):
    cwd = None
    messages = []
    with path.open(errors="replace") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            cwd = item.get("cwd") or cwd
            if item.get("isSidechain") or item.get("type") not in ("user", "assistant"):
                continue
            payload = item.get("message") or {}
            content = payload.get("content")
            if isinstance(content, str):
                parts = [content.strip()] if content.strip() else []
            elif isinstance(content, list):
                parts = [part.get("text", "").strip() for part in content
                         if isinstance(part, dict) and part.get("type") == "text" and part.get("text", "").strip()]
            else:
                parts = []
            if parts:
                messages.append((item["type"], "\n\n".join(parts)))
                if first_only and cwd and messages[0][0] == "user":
                    break
    if not cwd or not Path(cwd).is_dir():
        raise RuntimeError("Claude Code 任务的原工作目录已不存在")
    return Path(cwd), messages


def claude_tasks(count=8):
    tasks = []
    for path in claude_session_files(count):
        try:
            cwd, messages = claude_context(path, first_only=True)
            first_user = next((text for role, text in messages if role == "user"), None)
            if first_user:
                tasks.append({"id": path.stem, "title": " ".join(first_user.split())[:100],
                              "updatedAt": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                              "cwd": str(cwd)})
        except (OSError, RuntimeError):
            continue
        if len(tasks) >= count:
            break
    return tasks


def git_snapshot(cwd):
    def run(*args):
        try:
            result = subprocess.run(["/usr/bin/git", "-C", str(cwd), *args], capture_output=True,
                                    text=True, timeout=10)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None
    branch = run("branch", "--show-current")
    if branch is None:
        return "此目录不是 Git 仓库。"
    status = run("status", "--short", "--untracked-files=normal") or "工作区干净"
    diffstat = run("diff", "--stat") or "无已跟踪文件的未暂存差异"
    staged = run("diff", "--cached", "--stat") or "无已暂存差异"
    commit = run("rev-parse", "--short", "HEAD") or "无提交"
    return f"分支：{branch or '(detached)'}\n提交：{commit}\n文件状态：\n{status[:12000]}\n\n未暂存变更：\n{diffstat[:6000]}\n\n已暂存变更：\n{staged[:6000]}"


def handoff_content(source, title, task_id, cwd, messages):
    transcript = "\n\n".join(f"### {'用户' if role == 'user' else source}\n{text}" for role, text in messages)
    if len(transcript) > 90000:
        transcript = transcript[:10000] + "\n\n[中间较早的对话已省略；以当前项目文件为准]\n\n" + transcript[-80000:]
    return (f"# {source} 任务交接\n\n"
            f"任务：{title}\n来源：{source}\n任务 ID：{task_id}\n"
            f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S %z')}\n工作目录：{cwd}\n\n"
            "## 接手指令\n\n"
            "先检查当前目录中的项目说明和实际文件，再阅读以下对话与 Git 状态。"
            "总结当前目标、已完成事项和下一步，然后继续执行尚未完成的任务。"
            "不要重做已经完成的步骤；遇到对话与文件不一致，以当前文件为准。"
            "此交接包是本地快照，生成之后的新消息不在其中。\n\n"
            f"## 工作区状态\n\n```text\n{git_snapshot(cwd)}\n```\n\n"
            f"## 对话记录（{len(messages)} 条）\n\n{transcript}\n")


def prepare_handoff(profile, task_id):
    tasks = {item["id"]: item for item in recent_tasks(profile, count=1000)}
    if task_id not in tasks:
        raise RuntimeError("该任务不在此账号的本地任务列表中")
    cwd, messages = session_context(session_files(profile, task_id))
    if not messages:
        raise RuntimeError("这条任务没有可交接的对话文本")
    title = tasks[task_id]["title"]
    content = handoff_content("Codex " + settings()[profile], title, task_id, cwd, messages)
    path = ROOT / "handoffs" / f"{profile}-{task_id}.md"
    atomic_write(path, content.encode("utf-8"))
    return {"path": str(path), "cwd": str(cwd), "content": content,
            "messageCount": len(messages), "target": "claude", "sourceProfile": profile}


def prepare_codex_account_handoff(profile, task_id):
    if profile not in ("primary", "secondary"):
        raise ValueError("账号无效")
    tasks = recent_tasks(profile, count=1000)
    task = next((item for item in tasks if item["id"] == task_id), None)
    if not task:
        raise RuntimeError("找不到所选任务")
    cwd, messages = session_context(session_files(profile, task_id))
    content = handoff_content("Codex " + settings()[profile], task["title"], task_id, cwd, messages)
    path = ROOT / "handoffs" / f"codex-{profile}-{task_id}.md"
    atomic_write(path, content.encode("utf-8"))
    return {"path": str(path), "cwd": str(cwd), "content": content,
            "messageCount": len(messages), "target": "codex-account"}


def desktop_handoff_worker(path_text, cwd_text, target_profile, status_path_text):
    path = Path(path_text).resolve()
    if path.parent != (ROOT / "handoffs").resolve() or not path.is_file():
        raise ValueError("交接包路径无效")
    cwd = Path(cwd_text).resolve()
    if not cwd.is_dir():
        raise RuntimeError("原工作目录已不存在")
    if target_profile not in ("primary", "secondary"):
        raise ValueError("账号无效")
    status_path = Path(status_path_text).resolve()
    if status_path.parent != (ROOT / "handoffs").resolve():
        raise ValueError("状态文件路径无效")
    binary = codex_binary()
    if not binary:
        raise RuntimeError("找不到 Codex 运行程序")
    proc = subprocess.Popen([binary, "app-server", "--listen", "stdio://"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            env=dict(os.environ, CODEX_HOME=str(profile_home(target_profile))), bufsize=1)
    try:
        early_events = []
        def send(item):
            proc.stdin.write(json.dumps(item, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        def response(identifier):
            for line in proc.stdout:
                item = json.loads(line)
                if item.get("method") in ("turn/completed", "turn/failed"):
                    early_events.append(item.get("method"))
                if item.get("id") == identifier:
                    if item.get("error"):
                        raise RuntimeError(str(item["error"]))
                    return item.get("result") or {}
            raise RuntimeError("Codex app-server 已退出")
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "codex_duo", "title": "Codex Duo", "version": "0.3"}}})
        response(1)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "thread/start", "params": {"cwd": str(cwd),
              "sandbox": "workspace-write", "approvalPolicy": "never"}})
        started = response(2)
        thread_id = (started.get("thread") or {}).get("id")
        if not thread_id:
            raise RuntimeError("Codex 未返回新任务 ID")
        prompt = f"请阅读本地交接包 {path}，核对当前项目状态，然后继续原任务。先简短说明你理解的下一步。"
        send({"id": 3, "method": "turn/start", "params": {"threadId": thread_id,
              "input": [{"type": "text", "text": prompt}]}})
        response(3)
        atomic_write(status_path, json.dumps({"threadId": thread_id, "state": "started"}).encode())
        if early_events:
            atomic_write(status_path, json.dumps({"threadId": thread_id, "state": early_events[-1]}).encode())
            return
        for line in proc.stdout:
            item = json.loads(line)
            method = item.get("method") or ""
            if method in ("turn/completed", "turn/failed"):
                atomic_write(status_path, json.dumps({"threadId": thread_id, "state": method}).encode())
                break
    except Exception as exc:
        atomic_write(status_path, json.dumps({"state": "failed", "error": str(exc)}, ensure_ascii=False).encode())
        raise
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def launch_desktop_handoff(path_text, cwd_text, target_profile):
    if target_profile not in ("primary", "secondary"):
        raise ValueError("账号无效")
    if not (profile_home(target_profile) / "auth.json").is_file():
        raise RuntimeError("目标账号尚未登录")
    path = Path(path_text).resolve()
    if path.parent != (ROOT / "handoffs").resolve() or not path.is_file():
        raise ValueError("交接包路径无效")
    cwd = Path(cwd_text).resolve()
    if not cwd.is_dir():
        raise RuntimeError("原工作目录已不存在")
    status_path = ROOT / "handoffs" / f"desktop-{uuid.uuid4().hex}.json"
    log_path = ROOT / "handoffs" / f"desktop-{uuid.uuid4().hex}.log"
    private_dir(status_path.parent)
    with log_path.open("w") as log:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "desktop-handoff-worker",
                                 str(path), str(cwd), target_profile, str(status_path)],
                                stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(80):
        if status_path.exists():
            result = json.loads(status_path.read_text())
            if result.get("state") == "failed":
                raise RuntimeError(result.get("error") or "创建目标任务失败")
            return result
        if proc.poll() is not None:
            raise RuntimeError("创建目标任务失败，请查看交接日志")
        time.sleep(0.1)
    raise RuntimeError("创建目标任务超时，请查看交接日志")


def prepare_claude_handoff(task_id):
    task = next((item for item in claude_tasks(count=100) if item["id"] == task_id), None)
    if task is None:
        raise RuntimeError("找不到这条 Claude Code 任务")
    path = next((path for path in claude_session_files(count=100) if path.stem == task_id), None)
    if path is None:
        raise RuntimeError("找不到这条 Claude Code 会话记录")
    cwd, messages = claude_context(path)
    content = handoff_content("Claude Code", task["title"], task_id, cwd, messages)
    output = ROOT / "handoffs" / f"claude-{task_id}.md"
    atomic_write(output, content.encode("utf-8"))
    return {"path": str(output), "cwd": str(cwd), "content": content,
            "messageCount": len(messages), "target": "codex"}


def launch_handoff(path_text, cwd_text):
    path = Path(path_text).resolve()
    handoff_dir = (ROOT / "handoffs").resolve()
    if path.parent != handoff_dir or not path.is_file():
        raise ValueError("交接包路径无效")
    prompt = f"请阅读本地交接包 {path}，核对当前项目状态，然后继续这条任务。先简短说明你理解的下一步。"
    return launch_claude_cli(cwd_text, prompt)


def ensure_claude_onboarding():
    # Interactive first-run preflight probes Anthropic even with a custom base URL.
    # Only finish the welcome flow; keep project trust and tool permissions intact.
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    path = Path(config_dir).expanduser() / ".claude.json" if config_dir else Path.home() / ".claude.json"
    original = path.read_bytes() if path.exists() else None
    config = json.loads(original) if original else {}
    if config.get("hasCompletedOnboarding") is True:
        return
    if original is not None:
        backup = ROOT / "backups" / f"claude-onboarding-{time.time_ns()}.json"
        atomic_write(backup, original)
    config["hasCompletedOnboarding"] = True
    atomic_write(path, json.dumps(config, ensure_ascii=False, indent=2).encode())


def launch_claude_cli(cwd_text, prompt=None):
    cwd = Path(cwd_text)
    if not cwd.is_dir():
        raise RuntimeError("原工作目录已不存在")
    claude = shutil.which("claude") or next((str(p) for p in (
        Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")
    ) if p.is_file()), "")
    if not Path(claude).is_file():
        raise RuntimeError("找不到 Claude Code CLI，请先安装 Claude Code")
    gateway = gateway_settings()
    ensure_claude_onboarding()
    prompt_arg = " " + shlex.quote(prompt) if prompt else ""
    script = ROOT / "handoffs" / f"start-claude-{int(time.time() * 1000)}.command"
    body = (f"#!/bin/zsh\ncd {shlex.quote(str(cwd))} || exit 1\n"
            f"gateway_key=$(/usr/bin/security find-generic-password -s {shlex.quote(GATEWAY_KEYCHAIN_SERVICE)} -a default -w 2>/dev/null)\n"
            "if [[ -z \"$gateway_key\" ]]; then echo 'Codex Duo: 请先在设置中保存服务商密钥。'; read -k 1; exit 1; fi\n"
            f"export ANTHROPIC_BASE_URL={shlex.quote(gateway['url'])}\n"
            "export ANTHROPIC_API_KEY=\"$gateway_key\"\n"
            f"export ANTHROPIC_MODEL={shlex.quote(gateway['model'])}\n"
            f"export ANTHROPIC_DEFAULT_OPUS_MODEL={shlex.quote(gateway['model'])}\n"
            f"export ANTHROPIC_DEFAULT_SONNET_MODEL={shlex.quote(gateway['model'])}\n"
            f"export ANTHROPIC_DEFAULT_HAIKU_MODEL={shlex.quote(gateway['model'])}\n"
            "unset gateway_key\n"
            f"exec {shlex.quote(claude)} --model {shlex.quote(gateway['model'])}{prompt_arg}\n")
    atomic_write(script, body.encode("utf-8"))
    os.chmod(script, 0o700)
    result = subprocess.run(["open", "-a", "Terminal", str(script)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("启动终端失败：" + result.stderr.strip())
    return str(script)


def launch_codex_handoff(path_text, cwd_text, profile):
    path = Path(path_text).resolve()
    if path.parent != (ROOT / "handoffs").resolve() or not path.is_file():
        raise ValueError("交接包路径无效")
    cwd = Path(cwd_text)
    if not cwd.is_dir():
        raise RuntimeError("原工作目录已不存在")
    binary = codex_binary()
    if not binary or not Path(binary).is_file():
        raise RuntimeError("找不到 Codex CLI")
    home = profile_home(profile)
    if not (home / "auth.json").is_file():
        raise RuntimeError("所选 Codex 账号尚未登录")
    prompt = f"请阅读本地交接包 {path}，核对当前项目状态，然后继续这条任务。先简短说明你理解的下一步。"
    script = ROOT / "handoffs" / f"start-codex-{int(time.time() * 1000)}.command"
    body = (f"#!/bin/zsh\ncd {shlex.quote(str(cwd))} || exit 1\n"
            f"export CODEX_HOME={shlex.quote(str(home))}\nexec {shlex.quote(binary)} {shlex.quote(prompt)}\n")
    atomic_write(script, body.encode("utf-8"))
    os.chmod(script, 0o700)
    result = subprocess.run(["open", "-a", "Terminal", str(script)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("启动终端失败：" + result.stderr.strip())
    return str(script)


def history_path(profile):
    return ROOT / (profile + "-history.jsonl")


def quota_history(profile, count=48):
    path = history_path(profile)
    if not path.exists():
        return []
    points = []
    with path.open(errors="replace") as stream:
        for line in stream:
            try:
                points.append(json.loads(line))
            except ValueError:
                continue
    return points[-count:]


def append_history(profile, usage, checked_at):
    point = {"at": checked_at,
             "fiveHour": next((v["remaining"] for v in usage.values() if v["label"] == "5 小时"), None),
             "weekly": next((v["remaining"] for v in usage.values() if v["label"] == "每周"), None)}
    path = history_path(profile)
    private_dir(path.parent)
    with path.open("a") as stream:
        stream.write(json.dumps(point) + "\n")
    os.chmod(path, 0o600)


def status(refresh=False):
    names, pids = settings(), desktop_pids()
    output = []
    for profile in ("primary", "secondary"):
        cache = ROOT / (profile + "-usage.json")
        entry = {"id": profile, "name": names[profile], "pid": pids[profile],
                 "loggedIn": (profile_home(profile) / "auth.json").exists(),
                 "usage": None, "planType": None, "updatedAt": None, "error": None,
                 "recentTasks": recent_tasks(profile), "history": []}
        if refresh and entry["loggedIn"]:
            try:
                limits_result = read_limits(profile)
                limits = (limits_result.get("rateLimitsByLimitId") or {}).get("codex") or limits_result.get("rateLimits") or {}
                entry["planType"] = limits.get("planType")
                entry["usage"] = summarize(limits_result)
                entry["updatedAt"] = int(time.time())
                atomic_write(cache, json.dumps({"usage": entry["usage"], "planType": entry["planType"], "updatedAt": entry["updatedAt"]}).encode())
                append_history(profile, entry["usage"], entry["updatedAt"])
            except Exception as exc:
                entry["error"] = str(exc)
        if entry["usage"] is None and cache.exists():
            entry.update(json.loads(cache.read_text()))
        if not entry["loggedIn"]:
            entry["planType"] = None
        if pids[profile] and desktop_home(pids[profile]) != profile_home(profile).resolve():
            entry["error"] = "窗口的账号目录不匹配，请通过刷新窗口修复"
            entry["usage"] = None
        entry["history"] = quota_history(profile)
        output.append(entry)
    return output


def main():
    parser = argparse.ArgumentParser()
    cmds = parser.add_subparsers(dest="command", required=True)
    cmds.add_parser("status").add_argument("--refresh", action="store_true")
    cmds.add_parser("open").add_argument("profile", choices=("primary", "secondary"))
    cmds.add_parser("restart-window").add_argument("profile", choices=("primary", "secondary"))
    rename_cmd = cmds.add_parser("rename")
    rename_cmd.add_argument("profile", choices=("primary", "secondary"))
    rename_cmd.add_argument("name")
    gateway_cmd = cmds.add_parser("gateway")
    cmds.add_parser("shared-project")
    cmds.add_parser("sync-desktop-projects")
    cmds.add_parser("sync-desktop-workspace").add_argument("--refresh-windows", action="store_true")
    cmds.add_parser("desktop-projects")
    set_shared_cmd = cmds.add_parser("set-shared-project")
    set_shared_cmd.add_argument("path")
    launch_shared_cmd = cmds.add_parser("launch-shared-project")
    launch_shared_cmd.add_argument("profile", choices=("primary", "secondary"))
    set_gateway_cmd = cmds.add_parser("set-gateway")
    set_gateway_cmd.add_argument("url")
    set_gateway_cmd.add_argument("model")
    handoff_cmd = cmds.add_parser("handoff")
    handoff_cmd.add_argument("profile", choices=("primary", "secondary"))
    handoff_cmd.add_argument("task_id")
    account_handoff_cmd = cmds.add_parser("codex-account-handoff")
    account_handoff_cmd.add_argument("profile", choices=("primary", "secondary"))
    account_handoff_cmd.add_argument("task_id")
    desktop_handoff_cmd = cmds.add_parser("launch-desktop-handoff")
    desktop_handoff_cmd.add_argument("path")
    desktop_handoff_cmd.add_argument("cwd")
    desktop_handoff_cmd.add_argument("profile", choices=("primary", "secondary"))
    worker_cmd = cmds.add_parser("desktop-handoff-worker")
    worker_cmd.add_argument("path")
    worker_cmd.add_argument("cwd")
    worker_cmd.add_argument("profile", choices=("primary", "secondary"))
    worker_cmd.add_argument("status_path")
    claude_cmd = cmds.add_parser("claude-tasks")
    cmds.add_parser("claude-usage")
    claude_handoff_cmd = cmds.add_parser("claude-handoff")
    claude_handoff_cmd.add_argument("task_id")
    launch_cmd = cmds.add_parser("launch-handoff")
    launch_cmd.add_argument("path")
    launch_cmd.add_argument("cwd")
    launch_claude_cmd = cmds.add_parser("launch-claude")
    launch_claude_cmd.add_argument("cwd")
    launch_codex_cmd = cmds.add_parser("launch-codex-handoff")
    launch_codex_cmd.add_argument("path")
    launch_codex_cmd.add_argument("cwd")
    launch_codex_cmd.add_argument("profile", choices=("primary", "secondary"))
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(status(args.refresh), ensure_ascii=False))
    elif args.command == "open":
        print(open_profile(args.profile))
    elif args.command == "restart-window":
        print(restart_profile(args.profile))
    elif args.command == "gateway":
        print(json.dumps(gateway_settings(), ensure_ascii=False))
    elif args.command == "shared-project":
        print(json.dumps(shared_project(), ensure_ascii=False))
    elif args.command == "sync-desktop-projects":
        print(json.dumps(sync_desktop_projects(), ensure_ascii=False))
    elif args.command == "sync-desktop-workspace":
        print(json.dumps(sync_desktop_workspace(args.refresh_windows), ensure_ascii=False))
    elif args.command == "desktop-projects":
        print(json.dumps(desktop_project_overview(), ensure_ascii=False))
    elif args.command == "set-shared-project":
        print(json.dumps(save_shared_project(args.path), ensure_ascii=False))
    elif args.command == "launch-shared-project":
        print(launch_shared_project(args.profile))
    elif args.command == "set-gateway":
        save_gateway(args.url, args.model)
    elif args.command == "handoff":
        print(json.dumps(prepare_handoff(args.profile, args.task_id), ensure_ascii=False))
    elif args.command == "codex-account-handoff":
        print(json.dumps(prepare_codex_account_handoff(args.profile, args.task_id), ensure_ascii=False))
    elif args.command == "launch-desktop-handoff":
        print(json.dumps(launch_desktop_handoff(args.path, args.cwd, args.profile), ensure_ascii=False))
    elif args.command == "desktop-handoff-worker":
        desktop_handoff_worker(args.path, args.cwd, args.profile, args.status_path)
    elif args.command == "claude-tasks":
        print(json.dumps(claude_tasks(), ensure_ascii=False))
    elif args.command == "claude-usage":
        print(json.dumps(claude_usage(), ensure_ascii=False))
    elif args.command == "claude-handoff":
        print(json.dumps(prepare_claude_handoff(args.task_id), ensure_ascii=False))
    elif args.command == "launch-handoff":
        print(launch_handoff(args.path, args.cwd))
    elif args.command == "launch-claude":
        print(launch_claude_cli(args.cwd))
    elif args.command == "launch-codex-handoff":
        print(launch_codex_handoff(args.path, args.cwd, args.profile))
    else:
        rename(args.profile, args.name)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
