"""把本地知识库（索引/notes.jsonl）生成为 Obsidian 仓库。仓库路径、名称取自 config.json。

  python3 build_obsidian.py --kb <KB>

- 笔记与逐字稿按分类存放；仓库里已存在的同一 note_id 笔记跳过不覆盖（保护用户在 Obsidian 里的修改和移动）
- 导航页（00_导航/）、首页、人物与机构页每次重新生成
- 只新增/覆盖生成内容，不删除任何文件
- 看板按 config.mode：dual 两个都生成，enterprise 只生成企业提效看板，media 只生成自媒体选题库
"""
import collections
import json
import re
from pathlib import Path

from common import CATEGORIES, UNCLASSIFIED, kb_arg, load_config, ms_to_clock, read_jsonl, safe_name

NAV = "00_导航"
ENT = "人物与机构"
OUT = "AI产出"  # 之后让 AI 写的口播稿、SOP、复盘都存这里，和原始笔记分开
ALL_CATEGORIES = {UNCLASSIFIED: "尚未分类，需补充分类", **CATEGORIES}
CAT_COLORS = [0x8B8D98, 0xE5484D, 0xF76B15, 0xFFC53D, 0x30A46C, 0x12A594, 0x0090FF, 0x3E63DD,
              0x8E4EC6, 0xD6409F, 0xA18072, 0x8B8D98]
MIN_PEOPLE, MIN_ORGS = 2, 3  # 出现次数达到才建人物/机构页，避免一次性提及刷出几百个空页
DISPLAY = {"file.name": "标题", "note.source": "来源", "note.category": "分类", "note.type": "类型", "note.date": "日期",
           "note.duration_min": "时长(分钟)", "note.uses": "用途", "note.koubo_potential": "口播潜力",
           "note.topic_idea": "可做选题", "note.method_idea": "可沉淀方法", "note.todo_count": "待办数",
           "note.business_lines": "业务线", "note.industries": "行业", "note.people": "人物",
           "note.summary": "摘要", "note.confidence": "分类把握"}


def tag(prefix, value):
    value = re.sub(r"[\s/,#]+", "-", value).strip("-")
    return f"{prefix}/{value}" if value else None


def yq(s):
    return json.dumps(s, ensure_ascii=False)


def ylist(items):
    return "[" + ", ".join(yq(i) for i in items) + "]"


def disp(text):
    return re.sub(r"[|\[\]\n]", " ", text or "").strip()


def origin_link(r):
    if r.get("note_url"):
        return f"[{r['source_label']}]({r['note_url']})"
    if r.get("origin_path"):
        return f"{r['source_label']}（原文件 `{r['origin_path']}`）"
    return r["source_label"]


def cat_moc(category):
    return f"📂 {category[3:]}"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class Builder:
    def __init__(self, kb):
        self.kb = Path(kb)
        self.cfg = load_config(kb)
        self.vault = Path(self.cfg["vault"]).expanduser()
        self.home = f"🏠 {self.cfg['kb_name']}"
        self.people_alias = {**self.cfg["people_alias"], **{n: None for n in self.cfg["self_names"]}}
        self.org_alias = self.cfg["org_alias"]

    def normalize(self, values, alias):
        out = []
        for v in values:
            v = alias.get(v.strip(), v.strip())
            if v and v not in out:
                out.append(v)
        return out

    def load(self):
        records = read_jsonl(self.kb / "索引" / "notes.jsonl")
        for r in records:
            r["people"] = self.normalize(r["people"], self.people_alias)
            r["orgs"] = self.normalize(r["orgs"], self.org_alias)
            r["stem"] = r["md_path"].rsplit("/", 1)[-1][:-3]
        records.sort(key=lambda r: r["created_at"], reverse=True)
        return records

    def existing_notes(self):
        found = {}
        for p in self.vault.rglob("*.md"):
            if ".obsidian" in p.parts:
                continue
            head = p.read_text(encoding="utf-8", errors="ignore")[:600]
            m = re.search(r'^note_id: "?([^"\n]+?)"?$', head, re.M)
            k = re.search(r"^kind: (\S+)$", head, re.M)
            if m:
                found[(m.group(1), k.group(1) if k else "笔记")] = p
        return found

    # ---------- 单条笔记 ----------
    def note_markdown(self, r, people_pages, org_pages):
        tags = [tag("分类", r["category"][3:]), tag("来源", r["source_label"])] + [tag("用途", u) for u in r["uses"]]
        tags += [tag("业务线", b) for b in r["business_lines"]] + [tag("行业", i) for i in r["industries"]]
        if r["koubo_potential"]:
            tags.append(tag("口播潜力", r["koubo_potential"]))
        if r["method_idea"]:
            tags.append("可沉淀方法")
        if r["todos"]:
            tags.append("有待办")
        link = lambda vals, pages: [f"[[{v}]]" if v in pages else v for v in vals]
        created = r["created_at"][:19]
        fm = [
            "---",
            f"note_id: \"{r['note_id']}\"",
            "kind: 笔记",
            f"title: {yq(r['title'])}",
            f"category: {r['category']}",
            f"source: {r['source_label']}",
            f"type: {r['type_label']}",
            f"date: {r['date']}",
            f"created: {created}",
            f"duration_min: {r['duration_min'] or ''}",
            f"summary: {yq(r['summary'])}",
            f"uses: {ylist(r['uses'])}",
            f"koubo_potential: {r['koubo_potential']}",
            f"topic_idea: {yq(r['topic_idea'])}",
            f"method_idea: {yq(r['method_idea'])}",
            f"todo_count: {len(r['todos'])}",
            f"business_lines: {ylist(r['business_lines'])}",
            f"industries: {ylist(r['industries'])}",
            f"people: {ylist(link(r['people'], people_pages))}",
            f"orgs: {ylist(link(r['orgs'], org_pages))}",
            f"keywords: {ylist(r['keywords'])}",
            f"confidence: {r['confidence']}",
            f"url: {r['note_url']}",
            f"tags: {ylist([t for t in tags if t])}",
            "---",
            "",
        ]
        meta = f"{r['type_label']} · {created.replace('T', ' ')[:16]}"
        if r["duration_min"]:
            meta += f" · 约 {r['duration_min']} 分钟"
        body = [
            f"> [!abstract] {r['summary'] or r['title']}",
            f"> {meta}",
            f"> 分类：[[{cat_moc(r['category'])}|{r['category'][3:]}]]　来源：{origin_link(r)}"
            + (f"　逐字稿：[[{r['stem']}_逐字稿|打开]]" if r["transcript_path"] else ""),
        ]
        if r.get("web_url"):
            body.append(f"> 原网页：{r['web_url']}")
        if r["topic_idea"]:
            body += ["", f"> [!tip] 可做选题（口播潜力：{r['koubo_potential'] or '未评估'}）", f"> {r['topic_idea']}"]
        if r["method_idea"]:
            body += ["", "> [!example] 可沉淀方法", f"> {r['method_idea']}"]
        body += ["", re.sub(r"!\[[^\]]*\]\([^)]*\)", "", r["content"]).strip() or "（无正文）", ""]
        if r["chapters"]:
            body += ["## 章节时间线", ""] + [f"- `{ms_to_clock(c.get('start_ms'))}` {c.get('title', '')}" for c in r["chapters"]] + [""]
        if r["todos"]:
            body += ["## 会议待办", ""] + [f"- [{'x' if t.get('completed') else ' '}] {t.get('text', '')}" for t in r["todos"]] + [""]
        return "\n".join(fm + body)

    def transcript_markdown(self, r):
        return "\n".join([
            "---", f"note_id: \"{r['note_id']}\"", "kind: 逐字稿", f"title: {yq(r['title'])}",
            f"category: {r['category']}", f"source: {r['source_label']}", f"date: {r['date']}", f"url: {r['note_url']}", "---", "",
            f"摘要笔记：[[{r['stem']}]]", "", r["transcript"].strip(), "",
        ])

    # ---------- 表格视图（Obsidian Bases） ----------
    @staticmethod
    def base_properties():
        lines = ["properties:"]
        for k, v in DISPLAY.items():
            lines += [f"  {k}:", f"    displayName: {v}"]
        return lines

    def base_block(self, filters, views):
        """views: [(视图名, [额外过滤], [列])]，统一按创建时间倒序。"""
        lines = ["```base", "filters:", "  and:"] + [f"    - {f}" for f in filters] + self.base_properties() + ["views:"]
        for name, extra, cols in views:
            lines += ["  - type: table", f"    name: {name}"]
            if extra:
                lines += ["    filters:", "      and:"] + [f"        - {f}" for f in extra]
            lines += ["    order:"] + [f"      - {c}" for c in cols]
            lines += ["    sort:", "      - property: note.created", "        direction: DESC"]
        lines.append("```")
        return "\n".join(lines)

    def bullet(self, r, extra=""):
        dur = f"（{r['duration_min']}分钟）" if r["duration_min"] else ""
        return f"- {r['date']} [[{r['stem']}|{disp(r['title'])}]]{dur} — {extra or r['summary']}"

    # ---------- 导航 ----------
    def build_navigation(self, records, people_pages, org_pages):
        nav = self.vault / NAV
        home = self.home
        by_cat = collections.defaultdict(list)
        for r in records:
            by_cat[r["category"]].append(r)
        cols = ["file.name", "note.type", "note.date", "note.duration_min", "note.uses", "note.summary"]

        for cat, desc in ALL_CATEGORIES.items():
            items = by_cat.get(cat, [])
            if not items:
                continue
            lines = [f"> {desc}　共 {len(items)} 条　← 返回 [[{home}]]", "",
                     self.base_block([f'file.inFolder("{cat}")', 'note.kind == "笔记"'], [("表格", [], cols)]), "",
                     "## 按月份", ""]
            by_month = collections.defaultdict(list)
            for r in items:
                by_month[r["date"][:7]].append(r)
            for m in sorted(by_month, reverse=True):
                lines += [f"### {m}（{len(by_month[m])}）", ""] + [self.bullet(r) for r in by_month[m]] + [""]
            write(nav / "分类" / f"{cat_moc(cat)}.md", "\n".join(lines))

        def grouped(key, fname, intro):
            groups = collections.defaultdict(list)
            for r in records:
                for v in r[key]:
                    groups[v].append(r)
            lines = [f"> {intro}　← 返回 [[{home}]]", ""]
            for name, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
                lines += [f"## {name}（{len(items)}）", ""] + [self.bullet(r) for r in items] + [""]
            write(nav / f"{fname}.md", "\n".join(lines))

        grouped("business_lines", "按业务线", "一条笔记可属于多条业务线")
        grouped("industries", "按行业", "一条笔记可涉及多个行业")

        lines = [f"← 返回 [[{home}]]", ""]
        by_month = collections.defaultdict(list)
        for r in records:
            by_month[r["date"][:7]].append(r)
        for m in sorted(by_month, reverse=True):
            lines += [f"## {m}（{len(by_month[m])}）", ""]
            lines += [f"- {r['date']}｜[[{cat_moc(r['category'])}|{r['category'][3:]}]]｜[[{r['stem']}|{disp(r['title'])}]]" for r in by_month[m]] + [""]
        write(nav / "时间线.md", "\n".join(lines))

        low = [r for r in records if r["confidence"] == "low" or r["category"] == UNCLASSIFIED]
        lines = [f"> AI 分类把握较低或未分类的 {len(low)} 条。复核后把笔记拖到正确的分类文件夹，并改属性 category。← 返回 [[{home}]]", ""]
        lines += [f"- [[{r['stem']}|{disp(r['title'])}]]　当前：{r['category']}　{r['summary']}" for r in low]
        write(nav / "待复核的分类.md", "\n".join(lines) + "\n")

        if self.cfg["mode"] in ("dual", "enterprise"):
            self.enterprise_board(records, nav)
        if self.cfg["mode"] in ("dual", "media"):
            self.media_board(records, nav)

        for name in sorted(people_pages | org_pages):
            kind = "人物" if name in people_pages else "机构"
            items = [r for r in records if name in (r["people"] if kind == "人物" else r["orgs"])]
            aliases = [a for a, v in (self.people_alias if kind == "人物" else self.org_alias).items() if v == name]
            cats = collections.Counter(r["category"][3:] for r in items)
            lines = ["---", f"kind: {kind}", f"aliases: {ylist(aliases)}", f"mentions: {len(items)}", f"tags: [\"{kind}\"]", "---", "",
                     f"> {kind}｜在 {len(items)} 条笔记中出现｜{'、'.join(f'{k} {v}' for k, v in cats.most_common())}", "",
                     "## 相关笔记", ""] + [self.bullet(r) for r in items]
            write(self.vault / ENT / kind / f"{safe_name(name)}.md", "\n".join(lines) + "\n")

        top_people = sorted(people_pages, key=lambda p: -sum(p in r["people"] for r in records))
        top_orgs = sorted(org_pages, key=lambda o: -sum(o in r["orgs"] for r in records))
        write(nav / "人物与机构.md", "\n".join([
            f"> 在 {MIN_PEOPLE} 条及以上笔记中出现的人物、{MIN_ORGS} 条及以上的机构。← 返回 [[{home}]]", "",
            f"## 人物（{len(top_people)}）", "", "　".join(f"[[{p}]]" for p in top_people), "",
            f"## 机构与平台（{len(top_orgs)}）", "", "　".join(f"[[{o}]]" for o in top_orgs), "",
        ]))
        return by_cat

    def enterprise_board(self, records, nav):
        methods = [r for r in records if r["method_idea"]]
        todos = [r for r in records if r["todos"]][:20]
        block = self.base_block(['note.kind == "笔记"', 'file.hasTag("用途/企业提效")'], [
            ("可沉淀成方法/SOP", ['file.hasTag("可沉淀方法")'], ["file.name", "note.category", "note.date", "note.method_idea"]),
            ("客户沟通", ['file.inFolder("01_客户沟通")'], ["file.name", "note.date", "note.industries", "note.summary"]),
            ("有会议待办", ['file.hasTag("有待办")'], ["file.name", "note.category", "note.date", "note.todo_count", "note.summary"]),
            ("全部企业提效笔记", [], ["file.name", "note.category", "note.date", "note.summary"]),
        ])
        lines = [f"> 和工作效率有关的笔记都在这里。表格上方可切换视图。← 返回 [[{self.home}]]", "",
                 "> [!tip] 怎么用", "> 对 AI 说：「用我的知识库，把最近 10 次客户沟通整理成客户常见问题和回答话术」。",
                 f"> AI 写好的 SOP、话术、复盘会存到「{OUT}/企业提效」。", "", block, "",
                 f"## 可沉淀成方法/SOP（{len(methods)}）", ""] + [self.bullet(r, r["method_idea"]) for r in methods[:40]]
        lines += ["", "## 最近有待办的会议", ""] + [self.bullet(r, f"{len(r['todos'])} 项待办") for r in todos] + [""]
        write(nav / "企业提效看板.md", "\n".join(lines))

    def media_board(self, records, nav):
        rank = {"高": 0, "中": 1, "低": 2}
        # records 已按时间倒序，sorted 是稳定排序：先按潜力，同潜力里新的在前
        ideas = sorted([r for r in records if r["topic_idea"] and r["koubo_potential"] in ("高", "中")],
                       key=lambda r: rank[r["koubo_potential"]])
        block = self.base_block(['note.kind == "笔记"', 'file.hasTag("用途/自媒体素材")'], [
            ("口播潜力高", ['file.hasTag("口播潜力/高")'], ["file.name", "note.date", "note.topic_idea", "note.summary"]),
            ("全部素材", [], ["file.name", "note.category", "note.koubo_potential", "note.topic_idea"]),
            ("已写的内容作品", ['file.inFolder("07_内容作品")'], ["file.name", "note.date", "note.summary"]),
        ])
        lines = [f"> 能变成短视频口播的素材都在这里，按「口播潜力」排好了。← 返回 [[{self.home}]]", "",
                 "> [!tip] 怎么用", "> 挑一条选题，对 AI 说：「用我知识库里这条素材，写一条 60 秒视频号口播逐字稿」。",
                 f"> AI 写好的口播稿会存到「{OUT}/口播稿」。", "", block, "",
                 f"## 选题清单（潜力高/中，共 {len(ideas)} 条）", ""]
        lines += [f"- 【{r['koubo_potential']}】{r['topic_idea']}　← [[{r['stem']}|{disp(r['title'])}]]" for r in ideas[:80]]
        write(nav / "自媒体选题库.md", "\n".join(lines) + "\n")

    def build_home(self, records, by_cat):
        dates = sorted(r["date"] for r in records)
        minutes = sum(r["duration_min"] or 0 for r in records)
        mode = self.cfg["mode"]
        lines = [
            "> [!info] 关于这个知识库",
            f"> 共 {len(records)} 条笔记（{dates[0]} ～ {dates[-1]}），来自 "
            + "、".join(f"{k} {v} 条" for k, v in collections.Counter(r["source_label"] for r in records).most_common()) + "。",
            f"> 录音 {sum(r['is_audio'] for r in records)} 条" + (f"，累计约 {minutes // 60} 小时" if minutes else "") + "，有逐字稿的都能点开原文。",
            "",
        ]
        boards = []
        if mode in ("dual", "enterprise"):
            boards.append("- [[企业提效看板|🏢 企业提效看板]]：客户沟通、会议待办、可沉淀成 SOP 的方法")
        if mode in ("dual", "media"):
            boards.append("- [[自媒体选题库|🎙️ 自媒体选题库]]：按口播潜力排好的选题，挑一条就能写逐字稿")
        lines += ["## 两个看板" if len(boards) == 2 else "## 看板", ""] + boards
        lines += ["", "## 分类导航", "", "| 分类 | 数量 | 内容 |", "|---|---:|---|"]
        lines += [f"| [[{cat_moc(c)}]] | {len(by_cat[c])} | {d} |" for c, d in ALL_CATEGORIES.items() if by_cat.get(c)]
        lines += ["", "## 其他入口", "",
                  "- [[全部笔记.base|📊 全部笔记（表格，可筛选排序）]]",
                  "- [[按业务线]]　[[按行业]]　[[人物与机构]]　[[时间线]]　[[待复核的分类]]",
                  f"- {OUT}/：让 AI 写出来的口播稿、SOP、复盘", "", "## 最近 15 条", ""]
        lines += [self.bullet(r) for r in records[:15]]
        lines += ["", "## 使用小贴士", "",
                  "- `Cmd/Ctrl + O` 按标题打开笔记；`Cmd/Ctrl + Shift + F` 全文搜索（包括逐字稿）。",
                  "- 左侧「标签」面板可按 `用途/`、`口播潜力/`、`分类/` 筛选。",
                  "- 右上角「关系图谱」里不同颜色代表不同分类。", ""]
        write(self.vault / f"{self.home}.md", "\n".join(lines))

        write(self.vault / NAV / "全部笔记.base", "\n".join([
            "filters:", "  and:", '    - note.kind == "笔记"', *self.base_properties(), "views:",
            "  - type: table", "    name: 全部笔记", "    order:",
            *[f"      - {c}" for c in ["file.name", "note.source", "note.category", "note.date", "note.uses", "note.koubo_potential", "note.summary"]],
            "    sort:", "      - property: note.created", "        direction: DESC",
            "  - type: table", "    name: 按分类分组", "    groupBy:", "      property: note.category", "      direction: ASC",
            "    order:", *[f"      - {c}" for c in ["file.name", "note.date", "note.summary"]],
            "  - type: table", "    name: 录音", "    filters:", "      and:", "        - note.duration_min > 0",
            "    order:", *[f"      - {c}" for c in ["file.name", "note.category", "note.date", "note.duration_min", "note.summary"]],
            "",
        ]))

    def write_obsidian_config(self, by_cat):
        cfg = self.vault / ".obsidian"
        cfg.mkdir(parents=True, exist_ok=True)
        graph = cfg / "graph.json"
        if not graph.exists():
            groups = [{"query": f'path:"{c}"', "color": {"a": 1, "rgb": CAT_COLORS[i % len(CAT_COLORS)]}}
                      for i, c in enumerate(ALL_CATEGORIES) if by_cat.get(c)]
            groups.append({"query": f'path:"{ENT}"', "color": {"a": 1, "rgb": 0x5B5BD6}})
            graph.write_text(json.dumps({"colorGroups": groups, "showTags": False, "showAttachments": False,
                                         "search": f"-path:{NAV} -path:逐字稿"}, ensure_ascii=False, indent=2), encoding="utf-8")
        app = cfg / "app.json"
        if not app.exists():
            app.write_text(json.dumps({"alwaysUpdateLinks": True, "showUnsupportedFiles": False}, indent=2), encoding="utf-8")

    def run(self):
        records = self.load()
        pc = collections.Counter(p for r in records for p in r["people"])
        oc = collections.Counter(o for r in records for o in r["orgs"])
        people_pages = {p for p, c in pc.items() if c >= MIN_PEOPLE}
        org_pages = {o for o, c in oc.items() if c >= MIN_ORGS} - people_pages
        have = self.existing_notes()
        added = skipped = 0
        for r in records:
            folder = self.vault / r["category"]
            if (r["note_id"], "笔记") in have:
                skipped += 1
            else:
                write(folder / f"{r['stem']}.md", self.note_markdown(r, people_pages, org_pages))
                added += 1
            if r["transcript_path"] and (r["note_id"], "逐字稿") not in have:
                write(folder / "逐字稿" / f"{r['stem']}_逐字稿.md", self.transcript_markdown(r))
        for sub in ("口播稿", "企业提效"):
            (self.vault / OUT / sub).mkdir(parents=True, exist_ok=True)
        by_cat = self.build_navigation(records, people_pages, org_pages)
        self.build_home(records, by_cat)
        self.write_obsidian_config(by_cat)
        print(f"vault={self.vault}\nhome={self.home}.md\nnotes added={added} skipped(existing)={skipped} "
              f"people={len(people_pages)} orgs={len(org_pages)}")


    def preview_entities(self):
        """只打印人物/机构出现次数，用来在首次生成前发现同一对象的不同写法。"""
        records = self.load()
        for label, key in (("人物", "people"), ("机构", "orgs")):
            c = collections.Counter(v for r in records for v in r[key])
            print(f"## {label}（前 60）")
            print("、".join(f"{k} {v}" for k, v in c.most_common(60)))


if __name__ == "__main__":
    p = kb_arg()
    p.add_argument("--entities", action="store_true", help="只预览人物/机构，不写仓库")
    a = p.parse_args()
    b = Builder(a.kb)
    b.preview_entities() if a.entities else b.run()
