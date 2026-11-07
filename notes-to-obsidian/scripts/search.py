"""在本地知识库中检索笔记（标题、摘要、关键词、正文、逐字稿）。

  python3 search.py --kb <KB> 视频号定位
  python3 search.py --kb <KB> 报价 --category 02_合作洽谈 --limit 30
  python3 search.py --kb <KB> 宝妈 --koubo 高 --json      # 只看口播潜力高的，输出 JSON 便于继续分析

3 个字及以上用 FTS5 全文索引；更短的词（如「保险」）自动改用 LIKE 匹配。
"""
import json
import sqlite3
import sys
from pathlib import Path

from common import kb_arg


def main():
    p = kb_arg()
    p.add_argument("query")
    p.add_argument("--category")
    p.add_argument("--koubo", choices=["高", "中", "低"])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    db = Path(a.kb) / "kb.sqlite"
    if not db.exists():
        sys.exit(f"找不到 {db}：先运行 build_kb.py")

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    cols = ("n.note_id, n.date, n.category, n.title, n.summary, n.koubo_potential, n.topic_idea, "
            "n.method_idea, n.note_url, n.md_path, n.transcript_path")
    if len(a.query) >= 3:
        sql = f"select {cols} from notes_fts f join notes n using(note_id) where notes_fts match ?"
        params = ['"' + a.query.replace('"', '""') + '"']
    else:
        sql = f"select {cols} from notes n where (n.title || n.summary || n.keywords || n.content || n.transcript) like ?"
        params = [f"%{a.query}%"]
    if a.category:
        sql += " and n.category = ?"
        params.append(a.category)
    if a.koubo:
        sql += " and n.koubo_potential = ?"
        params.append(a.koubo)
    sql += " order by n.date desc limit ?"
    params.append(a.limit)
    rows = [dict(r) for r in con.execute(sql, params)]

    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    for r in rows:
        print(f"{r['date']}  {r['category']}  {r['title']}\n    {r['summary']}\n    {r['md_path']}")
    print(f"共 {len(rows)} 条（上限 {a.limit}）")


if __name__ == "__main__":
    main()
