"""把 normalized/*.jsonl（所有来源）切成供 AI 分类的批次。

  python3 build_catalog.py --kb <KB> [--batch-size 50] [--only-new]

输出 work/batch_XX.jsonl：每行 {note_id, source, title, type, created, text}
text 优先取正文/AI 总结，为空时取逐字稿开头；去掉图片，截断到 1500 字。
--only-new 只输出 work/classified.jsonl 里还没有的笔记（增量更新用）。
"""
import json
import re
import sys
from pathlib import Path

from common import IMG, kb_arg, load_normalized, read_jsonl

EXCERPT = 1500  # 实测 1500 字足够判断场景，50 条一批不会撑爆子 agent 上下文


def excerpt(rec):
    text = rec.get("content") or ""
    if not text.strip():
        text = rec.get("transcript") or ""
    text = IMG.sub("", text)
    return re.sub(r"\n{2,}", "\n", text).strip()[:EXCERPT]


def main():
    p = kb_arg()
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument("--only-new", action="store_true")
    a = p.parse_args()
    kb = Path(a.kb)
    work = kb / "work"
    work.mkdir(exist_ok=True)
    done = set()
    if a.only_new and (work / "classified.jsonl").exists():
        done = {r["note_id"] for r in read_jsonl(work / "classified.jsonl")}

    rows = []
    for rec in sorted(load_normalized(kb), key=lambda r: r["created_at"], reverse=True):
        if rec["note_id"] in done:
            continue
        rows.append({
            "note_id": rec["note_id"],
            "source": rec["source_label"],
            "title": rec["title"],
            "type": rec["type_label"],
            "created": rec["date"],
            "text": excerpt(rec),
        })
    if not rows and not done:
        sys.exit("normalized/ 里没有笔记：先运行某个导入脚本（fetch_getnote.py / import_files.py / import_feishu.py）")
    for old in work.glob("batch_*.jsonl"):
        old.unlink()
    for b in range(0, len(rows), a.batch_size):
        out = work / f"batch_{b // a.batch_size + 1:02d}.jsonl"
        chunk = rows[b:b + a.batch_size]
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in chunk), encoding="utf-8")
        print(out, len(chunk))
    print(f"待分类 {len(rows)} 条")


if __name__ == "__main__":
    main()
