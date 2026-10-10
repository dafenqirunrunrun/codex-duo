"""Local Claude Code token counters. These are not provider billing or balance."""

import json
from datetime import datetime, timedelta
from pathlib import Path

FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def read_records(path):
    records = {}
    with path.open(errors="replace") as stream:
        for index, line in enumerate(stream):
            try:
                item = json.loads(line)
                if item.get("type") != "assistant" or item.get("isApiErrorMessage"):
                    continue
                message = item.get("message") or {}
                usage = message.get("usage")
                if not isinstance(usage, dict) or message.get("model") == "<synthetic>":
                    continue
                at = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).timestamp()
                counts = [max(0, int(usage.get(field) or 0)) for field in FIELDS]
                if not any(counts):
                    continue
                # Streaming blocks and forked histories can repeat the same response.
                key = str(message.get("id") or item.get("uuid") or f"{path}:{index}")
                record = {"at": at, "counts": counts}
                merge_record(records, key, record)
            except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                continue
    return records


def merge_record(records, key, new):
    old = records.get(key)
    records[key] = new if old is None else {
        "at": min(old["at"], new["at"]),
        "counts": [max(a, b) for a, b in zip(old["counts"], new["counts"])],
    }


def local_usage(base, cache=None, now=None):
    now = now or datetime.now().astimezone()
    today = now.date()
    dates = [today - timedelta(days=n) for n in range(6, -1, -1)]
    days = {str(day): {"date": str(day), "input": 0, "output": 0, "cache": 0,
                       "total": 0, "requests": 0} for day in dates}
    previous = (cache or {}).get("files", {})
    files, records, errors = {}, {}, 0
    if Path(base).exists():
        for path in Path(base).glob("**/*.jsonl"):
            try:
                stat = path.stat()
                stamp = [stat.st_mtime_ns, stat.st_size]
                saved = previous.get(str(path), {})
                entries = saved["records"] if saved.get("stamp") == stamp else read_records(path)
                files[str(path)] = {"stamp": stamp, "records": entries}
                for key, record in entries.items():
                    merge_record(records, key, record)
            except (OSError, ValueError, TypeError, KeyError):
                errors += 1
    latest = None
    for record in records.values():
        at = record["at"]
        if at > now.timestamp():
            continue
        latest = max(latest or at, at)
        day = days.get(str(datetime.fromtimestamp(at, now.tzinfo).date()))
        if day is None:
            continue
        incoming, outgoing, created, read = record["counts"]
        day["input"] += incoming
        day["output"] += outgoing
        day["cache"] += created + read
        day["total"] += incoming + outgoing + created + read
        day["requests"] += 1
    week = {key: sum(day[key] for day in days.values()) for key in ("input", "output", "cache", "total", "requests")}
    result = {"today": days[str(today)], "week": week, "days": list(days.values()),
              "updatedAt": int(now.timestamp()), "lastActivityAt": int(latest) if latest else None,
              "filesRead": len(files), "unreadableFiles": errors}
    return result, {"version": 1, "files": files}
