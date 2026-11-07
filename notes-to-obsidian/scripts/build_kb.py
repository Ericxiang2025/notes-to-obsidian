"""把 normalized/*.jsonl（所有来源）+ work/classified.jsonl 组装成本地知识库（Obsidian 仓库的数据源）。

  python3 build_kb.py --kb <KB>

输出（均在 KB 下，每次整体重建这三个目录）：
  知识库/<分类>/<日期>_<标题>.md   每条笔记：元数据 + 摘要 + 正文/AI 总结 + 章节/待办
  逐字稿/<日期>_<标题>.md          录音逐字稿
  索引/notes.jsonl                 全量机器可读数据（build_obsidian.py 读它）
  索引/catalog.csv                 表格目录（Excel 可打开）
  kb.sqlite                        SQLite FTS5 全文检索库（trigram，支持中文）
  README.md                        总览
"""
import collections
import csv
import json
import shutil
import sqlite3
from pathlib import Path

from common import (CATEGORIES, IMG, UNCLASSIFIED, kb_arg, load_config, load_normalized, ms_to_clock,
                    read_jsonl, safe_name)

ALL_CATEGORIES = {UNCLASSIFIED: "尚未分类或分类结果无效，需补充", **CATEGORIES}


def yaml_list(items):
    return "[" + ", ".join(json.dumps(i, ensure_ascii=False) for i in items) + "]"


def load(kb):
    cls_path = kb / "work" / "classified.jsonl"
    cls = {r["note_id"]: r for r in read_jsonl(cls_path)} if cls_path.exists() else {}
    records = []
    for r in load_normalized(kb):
        c = cls.get(r["note_id"]) or {}
        r.update({
            "category": c.get("category") if c.get("category") in CATEGORIES else UNCLASSIFIED,
            "industries": c.get("industries") or [],
            "business_lines": c.get("business_lines") or [],
            "people": c.get("people") or [],
            "orgs": c.get("orgs") or [],
            "keywords": c.get("keywords") or [],
            "summary": c.get("summary") or "",
            "confidence": c.get("confidence") or "",
            "uses": c.get("uses") or [],
            "koubo_potential": c.get("koubo_potential") or "",
            "topic_idea": c.get("topic_idea") or "",
            "method_idea": c.get("method_idea") or "",
        })
        records.append(r)
    records.sort(key=lambda r: r["created_at"], reverse=True)
    return records


def assign_paths(records):
    used = set()
    for r in records:
        base = f"{r['date']}_{safe_name(r['title'])}"
        name = base if base not in used else f"{base}_{r['note_id'][-6:]}"
        used.add(name)
        r["file_stem"] = name
        r["md_path"] = f"知识库/{r['category']}/{name}.md"
        r["transcript_path"] = f"逐字稿/{name}.md" if r["transcript"].strip() else None


def write_note(kb, r):
    lines = [
        "---",
        f"note_id: \"{r['note_id']}\"",
        f"title: {json.dumps(r['title'], ensure_ascii=False)}",
        f"category: {r['category']}",
        f"source: {r['source_label']}",
        f"type: {r['type_label']}",
        f"created: {r['created_at']}",
        f"uses: {yaml_list(r['uses'])}",
        f"koubo_potential: {r['koubo_potential']}",
        f"keywords: {yaml_list(r['keywords'])}",
        f"url: {r['note_url']}",
        "---",
        "",
        f"# {r['title']}",
        "",
        f"> {r['summary']}" if r["summary"] else "",
        "",
        f"- 类型：{r['type_label']}　时间：{r['created_at'][:16].replace('T', ' ')}"
        + (f"　时长：约 {r['duration_min']} 分钟" if r["duration_min"] else ""),
        f"- 来源：{r['source_label']}　原笔记：{r['note_url'] or r['origin_path'] or '无'}",
    ]
    if r["topic_idea"]:
        lines.append(f"- 可做选题：{r['topic_idea']}")
    if r["method_idea"]:
        lines.append(f"- 可沉淀方法：{r['method_idea']}")
    if r["transcript_path"]:
        lines.append(f"- 逐字稿：{r['transcript_path']}")
    lines += ["", "## 正文 / AI 总结", "", IMG.sub("", r["content"]).strip() or "（无正文）", ""]
    if r["chapters"]:
        lines += ["## 章节时间线", ""] + [f"- {ms_to_clock(c.get('start_ms'))} {c.get('title', '')}" for c in r["chapters"]] + [""]
    if r["todos"]:
        lines += ["## 会议待办", ""] + [f"- [ ] {t.get('text') or t.get('title') or ''}" for t in r["todos"]] + [""]
    path = kb / r["md_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    if r["transcript_path"]:
        (kb / r["transcript_path"]).write_text(
            f"# 逐字稿｜{r['title']}\n\n{r['created_at']}　{r['note_url']}\n\n{r['transcript'].strip()}\n", encoding="utf-8")


def write_indexes(kb, records):
    idx = kb / "索引"
    idx.mkdir(exist_ok=True)
    with (idx / "notes.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps({k: v for k, v in r.items() if k != "file_stem"}, ensure_ascii=False) + "\n")
    with (idx / "catalog.csv").open("w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig：Excel 双击打开不乱码
        w = csv.writer(f)
        w.writerow(["日期", "标题", "来源", "分类", "类型", "时长(分钟)", "用途", "口播潜力", "可做选题", "可沉淀方法",
                    "行业", "业务线", "人物", "机构", "关键词", "一句话摘要", "分类把握", "原笔记链接", "本地文件"])
        for r in sorted(records, key=lambda r: r["created_at"], reverse=True):
            w.writerow([r["date"], r["title"], r["source_label"], r["category"], r["type_label"], r["duration_min"] or "",
                        "、".join(r["uses"]), r["koubo_potential"], r["topic_idea"], r["method_idea"],
                        "、".join(r["industries"]), "、".join(r["business_lines"]), "、".join(r["people"]),
                        "、".join(r["orgs"]), "、".join(r["keywords"]), r["summary"], r["confidence"],
                        r["note_url"], r["md_path"]])


def write_sqlite(kb, records):
    db = kb / "kb.sqlite"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    con.execute("""create table notes (note_id text primary key, title text, date text, created_at text,
        category text, source text, duration_min integer, uses text, koubo_potential text, topic_idea text,
        method_idea text, industries text, business_lines text, people text, orgs text, keywords text,
        summary text, note_url text, md_path text, transcript_path text, content text, transcript text)""")
    # trigram 分词支持中文子串匹配，但查询词至少 3 个字；更短的词 search.py 自动改用 LIKE
    con.execute("""create virtual table notes_fts using fts5(note_id unindexed, title, summary, keywords,
        content, transcript, tokenize='trigram')""")
    for r in records:
        j = lambda k: "、".join(r[k])
        con.execute("insert into notes values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            r["note_id"], r["title"], r["date"], r["created_at"], r["category"], r["source_label"], r["duration_min"],
            j("uses"), r["koubo_potential"], r["topic_idea"], r["method_idea"], j("industries"),
            j("business_lines"), j("people"), j("orgs"), j("keywords"), r["summary"], r["note_url"],
            r["md_path"], r["transcript_path"], r["content"], r["transcript"]))
        con.execute("insert into notes_fts values (?,?,?,?,?,?)", (
            r["note_id"], r["title"], r["summary"], j("keywords"), r["content"], r["transcript"]))
    con.commit()
    con.close()


def write_readme(kb, cfg, records):
    cat = collections.Counter(r["category"] for r in records)
    audio = [r for r in records if r["is_audio"]]
    src = collections.Counter(r["source_label"] for r in records)
    minutes = sum(r["duration_min"] or 0 for r in records)
    dates = sorted(r["date"] for r in records)
    lines = [
        f"# {cfg['kb_name']}（数据目录）",
        "",
        f"共 {len(records)} 条笔记，{dates[0]} ～ {dates[-1]}；录音 {len(audio)} 条"
        f"（有逐字稿 {sum(1 for r in audio if r['transcript_path'])} 条），累计约 {minutes // 60} 小时。",
        "来源：" + "、".join(f"{k} {v} 条" for k, v in src.most_common()) + "。全部只读导出，没有改动任何平台上的原数据。",
        "日常请在 Obsidian 里看，本目录供 AI 检索和分析。",
        "",
        "| 分类 | 数量 | 说明 |",
        "|---|---:|---|",
    ] + [f"| {k} | {cat.get(k, 0)} | {v} |" for k, v in ALL_CATEGORIES.items() if cat.get(k) or k != UNCLASSIFIED]
    lines += ["", "- 索引/catalog.csv：全部笔记目录表，Excel 可直接打开筛选",
              "- 索引/notes.jsonl：全量数据（含正文与逐字稿），适合交给 AI 分析",
              "- kb.sqlite：全文检索库", ""]
    (kb / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    kb = Path(kb_arg().parse_args().kb)
    cfg = load_config(kb)
    records = load(kb)
    if not records:
        raise SystemExit("没有可用笔记：先运行导入脚本（fetch_getnote.py / import_files.py）")
    assign_paths(records)
    for d in ("知识库", "逐字稿", "索引"):
        if (kb / d).exists():
            shutil.rmtree(kb / d)
    (kb / "逐字稿").mkdir(parents=True)
    for r in records:
        write_note(kb, r)
    write_indexes(kb, records)
    write_sqlite(kb, records)
    write_readme(kb, cfg, records)
    cat = collections.Counter(r["category"] for r in records)
    print(f"built {len(records)} notes；待分类 {cat.get(UNCLASSIFIED, 0)} 条")
    print("，".join(f"{k} {v}" for k, v in sorted(cat.items())))


if __name__ == "__main__":
    main()
