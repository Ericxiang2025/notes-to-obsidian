"""校验并合并 AI 分类结果。

  python3 merge_classified.py --kb <KB>

读 work/out_*.jsonl（每批分类结果）+ 已有的 work/classified.jsonl，按 note_id 合并（新结果覆盖旧的），
修正常见格式问题后写回 work/classified.jsonl。
退出码 0 = 所有批次里的笔记都有合法分类；1 = 有缺失或非法行，按打印的 note_id 重新分类对应批次。
"""
import json
import sys
from pathlib import Path

from common import CATEGORIES, POTENTIAL, USES, kb_arg, read_jsonl

LIST_FIELDS = ("industries", "business_lines", "people", "orgs", "keywords", "uses")
TEXT_FIELDS = ("summary", "topic_idea", "method_idea")


def clean(row):
    """返回 (规范化后的行, 问题说明)；问题说明非空表示这行不可用。"""
    if row.get("category") not in CATEGORIES:
        return None, f"category 非法：{row.get('category')}"
    for k in LIST_FIELDS:
        v = row.get(k) or []
        row[k] = [str(x).strip() for x in (v if isinstance(v, list) else [v]) if str(x).strip() and str(x).strip() != "无"]
    row["uses"] = [u for u in row["uses"] if u in USES]
    row["business_lines"] = [b for b in row["business_lines"] if b != "其他"]  # 「其他」不做标签，避免一个大杂烩分组
    for k in TEXT_FIELDS:
        row[k] = str(row.get(k) or "").strip()
    if row.get("koubo_potential") not in POTENTIAL:
        row["koubo_potential"] = "低"
    if row.get("confidence") not in ("high", "medium", "low"):
        row["confidence"] = "low"
    return row, ""


def main():
    kb = Path(kb_arg().parse_args().kb)
    work = kb / "work"
    expected = set()
    for b in work.glob("batch_*.jsonl"):
        expected |= {r["note_id"] for r in read_jsonl(b)}
    merged = {}
    if (work / "classified.jsonl").exists():
        merged = {r["note_id"]: r for r in read_jsonl(work / "classified.jsonl")}

    bad = []
    for out in sorted(work.glob("out_*.jsonl")):
        for n, line in enumerate(out.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("```"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad.append(f"{out.name}:{n} 不是合法 JSON")
                continue
            nid = str(row.get("note_id", ""))
            row, problem = clean(row)
            if problem:
                bad.append(f"{out.name}:{n} {nid} {problem}")
                continue
            row["note_id"] = nid
            merged[nid] = row

    missing = sorted(expected - merged.keys())
    (work / "classified.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in merged.values()), encoding="utf-8")

    cats = {}
    for r in merged.values():
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    print(f"已分类 {len(merged)} 条；分布：" + "，".join(f"{k} {v}" for k, v in sorted(cats.items())))
    print(f"低把握 {sum(r.get('confidence') == 'low' for r in merged.values())} 条；"
          f"口播潜力高 {sum(r.get('koubo_potential') == '高' for r in merged.values())} 条")
    for b in bad:
        print("BAD", b)
    if missing:
        print(f"MISSING {len(missing)} 条没有分类结果：{missing[:20]}")
    sys.exit(1 if bad or missing else 0)


if __name__ == "__main__":
    main()
