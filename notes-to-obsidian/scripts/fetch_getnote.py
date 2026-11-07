"""只读导出得到大脑全部笔记与录音逐字稿，可断点续跑。

  python3 fetch_getnote.py --kb <KB>                   先刷新笔记列表，再逐条抓详情（已下载的跳过）
  python3 fetch_getnote.py --kb <KB> --reverse --no-list   第二个进程从末尾往前抓，并行可提速一倍
  python3 fetch_getnote.py --kb <KB> --normalize-only   不联网，只把已下载的数据转成统一格式

输出：
  raw/getnote/notes_list.json             getnote notes --all（列表接口不含正文，所以还要逐条抓）
  raw/getnote/notes/<note_id>.json        getnote note <id>
  raw/getnote/transcripts/<note_id>.json  getnote note transcript <id>（仅录音类）
  raw/getnote/fetch_errors*.json          最终仍失败的条目（空白录音写空逐字稿占位，不算失败）
  normalized/getnote.jsonl                统一格式笔记；每次运行结束都重写，最后结束的进程写出的是全量
"""
import json
import subprocess
import sys
import time
from pathlib import Path

from common import AUDIO_TYPES, TYPE_LABEL, kb_arg, note_record, write_normalized

INTERVAL = 0.35  # 读接口限 5 次/秒；两个进程并行时合计约 5.7 次/秒，实测未触发限流
MAX_ATTEMPTS = 4


def run(args):
    try:
        p = subprocess.run(["getnote", *args, "-o", "json"], capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        sys.exit("找不到 getnote 命令：先运行 npm install -g @getnote/cli@latest")
    except subprocess.TimeoutExpired:
        return False, {"success": False, "error": {"message": "timeout", "retryable": True}}
    # 成功时 JSON 在 stdout；失败时 CLI 把错误 JSON 打到 stderr
    out = None
    for stream in (p.stdout, p.stderr):
        try:
            out = json.loads(stream)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(out, dict):
        out = {"success": False, "error": {"message": (p.stdout + p.stderr)[-800:]}}
    return p.returncode == 0 and out.get("success") is True, out


def fetch(args, dest, errors, key):
    if dest.exists():
        return
    for attempt in range(1, MAX_ATTEMPTS + 1):
        ok, out = run(args)
        time.sleep(INTERVAL)
        if ok:
            dest.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            errors.pop(key, None)
            return
        err = out.get("error") or {}
        if "not available" in str(err.get("message", "")):
            # 空白录音没有逐字稿：写空占位，以后重跑直接跳过，不再每次重试
            dest.write_text(json.dumps({"success": True, "data": {"transcript": ""}, "blank": True}), encoding="utf-8")
            errors.pop(key, None)
            return
        errors[key] = {"args": args, "error": err, "request_id": out.get("request_id")}
        print(f"FAIL {key} attempt={attempt} {json.dumps(err, ensure_ascii=False)[:300]}", flush=True)
        if err.get("retryable") is False and err.get("code") not in ("rate_limited", "RATE_LIMITED"):
            return
        time.sleep(min(30, 2 ** attempt))


def normalize(kb, raw):
    listed = json.loads((raw / "notes_list.json").read_text(encoding="utf-8"))["data"]["notes"]
    records, missing = [], 0
    for item in listed:
        nid = item["note_id"]
        npath = raw / "notes" / f"{nid}.json"
        if not npath.exists():
            missing += 1
            continue
        note = json.loads(npath.read_text(encoding="utf-8"))["data"]["note"]
        tpath = raw / "transcripts" / f"{nid}.json"
        transcript = ""
        if tpath.exists():
            transcript = json.loads(tpath.read_text(encoding="utf-8"))["data"].get("transcript") or ""
        if not transcript:
            transcript = (note.get("audio") or {}).get("original") or ""
        body = note.get("content") or ""
        if note["note_type"] == "link":
            body = (note.get("web_page") or {}).get("content") or body
        duration = max((a.get("duration") or 0 for a in note.get("attachments") or [] if a.get("type") == "audio"), default=0)
        records.append(note_record(
            note_id=nid, source="getnote",
            title=note.get("display_title") or note.get("title") or "未命名",
            type_label=TYPE_LABEL.get(note["note_type"], note["note_type"]),
            is_audio=note["note_type"] in AUDIO_TYPES,
            created_at=note["timestamps"]["created_at"],
            note_url=note["note_url"],
            web_url=(note.get("web_page") or {}).get("url")
            or next((a.get("url") for a in note.get("attachments") or [] if a.get("type") == "link"), None),
            duration_min=round(duration / 60000) if duration else None,
            source_tags=[t["name"] for t in note.get("tags") or []],
            content=body, transcript=transcript,
            chapters=(note.get("chapter_timeline") or {}).get("items") or [],
            todos=(note.get("meeting_todos") or {}).get("items") or [],
        ))
    out = write_normalized(kb, "getnote", records)
    print(f"统一格式 {len(records)} 条 → {out}" + (f"（还有 {missing} 条详情没下载，重跑本脚本）" if missing else ""), flush=True)


def main():
    p = kb_arg()
    p.add_argument("--reverse", action="store_true")
    p.add_argument("--no-list", action="store_true")
    p.add_argument("--normalize-only", action="store_true")
    a = p.parse_args()
    raw = Path(a.kb) / "raw" / "getnote"
    if a.normalize_only:
        return normalize(a.kb, raw)
    for d in (raw / "notes", raw / "transcripts"):
        d.mkdir(parents=True, exist_ok=True)

    list_path = raw / "notes_list.json"
    if not a.no_list:
        ok, out = run(["notes", "--all", "--page-size", "100"])
        if not ok:
            sys.exit(f"读取笔记列表失败：{json.dumps(out.get('error'), ensure_ascii=False)}\n"
                     "先运行 getnote doctor -o json 查看是否未登录或网络不通")
        list_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    if not list_path.exists():
        sys.exit("还没有 raw/getnote/notes_list.json：去掉 --no-list 先运行一次")

    notes = json.loads(list_path.read_text(encoding="utf-8"))["data"]["notes"]
    print(f"笔记共 {len(notes)} 条，其中录音类 {sum(n['note_type'] in AUDIO_TYPES for n in notes)} 条", flush=True)
    if a.reverse:
        notes = notes[::-1]
    err_path = raw / ("fetch_errors_rev.json" if a.reverse else "fetch_errors.json")
    errors = json.loads(err_path.read_text(encoding="utf-8")) if err_path.exists() else {}
    for i, n in enumerate(notes, 1):
        nid = n["note_id"]
        fetch(["note", nid], raw / "notes" / f"{nid}.json", errors, f"note:{nid}")
        if n["note_type"] in AUDIO_TYPES:
            fetch(["note", "transcript", nid], raw / "transcripts" / f"{nid}.json", errors, f"transcript:{nid}")
        if i % 20 == 0:
            print(f"{i}/{len(notes)} errors={len(errors)}", flush=True)
            err_path.write_text(json.dumps(errors, ensure_ascii=False, indent=1), encoding="utf-8")
    err_path.write_text(json.dumps(errors, ensure_ascii=False, indent=1), encoding="utf-8")
    done = sum((raw / "notes" / f"{n['note_id']}.json").exists() for n in notes)
    print(f"done 详情已下载 {done}/{len(notes)} errors={len(errors)}", flush=True)
    normalize(a.kb, raw)


if __name__ == "__main__":
    main()
