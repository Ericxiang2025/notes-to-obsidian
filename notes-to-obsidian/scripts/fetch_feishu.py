"""只读导出飞书「知识库（Wiki）」和「云空间（我的文档）」里的文档，落成带 front matter 的 md，再交给 import_files.py。

  python3 fetch_feishu.py --kb <KB>                         知识库（个人文档库 + 所有能看到的知识空间）+ 云空间
  python3 fetch_feishu.py --kb <KB> --only wiki             只导知识库；--only drive 只导云空间
  python3 fetch_feishu.py --kb <KB> --space my_library      知识库只导某个空间（可重复；space_id 用 --list-spaces 查）
  python3 fetch_feishu.py --kb <KB> --list-spaces           只列出知识空间，不下载

前提：已装官方 lark-cli 并以用户身份登录（lark-cli auth status 里 user 为 ready）。全程只调读接口。
输出：KB/inbox/feishu/<文档 token>.md（文件名用 token，改标题也不会变成新笔记）。
     文档没改过（编辑时间不变）就跳过；最后自动运行 import_files.py --source feishu。
上传到云空间的 .pdf .docx .md .txt .srt 文件下载到 KB/inbox/feishu/files/<token>/，由 import_files.py 读文字。
多维表格、电子表格、思维导图、图片、PPT 等不导出，只在结尾列出来。
"""
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from common import kb_arg

SCRIPTS = Path(__file__).resolve().parent
INTERVAL = 0.2
FILE_SUFFIXES = (".pdf", ".docx", ".md", ".txt", ".srt", ".vtt")


def lark(args, cwd=None):
    try:
        p = subprocess.run(["lark-cli", *args, "--as", "user"], capture_output=True, text=True, timeout=180, cwd=cwd)
    except FileNotFoundError:
        sys.exit("找不到 lark-cli：先运行 npx @larksuite/cli@latest install，再 lark-cli auth login --recommend")
    time.sleep(INTERVAL)
    for stream in (p.stdout, p.stderr):
        try:
            out = json.loads(stream)
            if isinstance(out, dict):
                return out
        except json.JSONDecodeError:
            continue
    return {"ok": False, "error": (p.stdout + p.stderr)[-500:]}


def must(out, what):
    if not out.get("ok"):
        sys.exit(f"{what}失败：{json.dumps(out.get('error'), ensure_ascii=False)[:500]}\n"
                 "先运行 lark-cli auth status 看用户身份是否 ready，不是就 lark-cli auth login --recommend")
    return out["data"]


def spaces():
    lib = must(lark(["wiki", "spaces", "get", "--params", '{"space_id":"my_library"}']), "读取个人文档库")["space"]
    found = [{"space_id": lib["space_id"], "name": "个人文档库"}]
    data = must(lark(["wiki", "+space-list", "--page-all", "--page-limit", "0"]), "列知识空间")
    found += [{"space_id": s["space_id"], "name": s.get("name") or s["space_id"]} for s in data.get("spaces") or []]
    return found


def walk(space_id, parent="", path=()):
    """逐层列节点（接口不递归）。返回 (节点, 目录路径) 列表。"""
    nodes, token = [], ""
    while True:
        params = {"page_size": 50}
        if parent:
            params["parent_node_token"] = parent
        if token:
            params["page_token"] = token
        data = must(lark(["api", "GET", f"/open-apis/wiki/v2/spaces/{space_id}/nodes",
                          "--params", json.dumps(params)]), "列知识库节点")
        for n in data.get("items") or []:
            nodes.append((n, path))
            if n.get("has_child"):
                nodes += walk(space_id, n["node_token"], path + ((n.get("title") or "").strip(),))
        token = data.get("page_token") if data.get("has_more") else ""
        if not token:
            return nodes


def walk_drive(folder="", path=("云空间",)):
    """逐层列云空间文件夹。返回统一条目列表。"""
    items, token = [], ""
    while True:
        params = {"page_size": 50}
        if folder:
            params["folder_token"] = folder
        if token:
            params["page_token"] = token
        data = must(lark(["api", "GET", "/open-apis/drive/v1/files", "--params", json.dumps(params)]), "列云空间文件")
        for f in data.get("files") or []:
            if f.get("type") == "folder":
                items += walk_drive(f["token"], path + ((f.get("name") or "").strip(),))
            elif f.get("type") == "shortcut":
                continue
            else:
                items.append({"token": f["token"], "obj": f["token"], "type": f.get("type"), "title": f.get("name"),
                              "created": f.get("created_time"), "edited": f.get("modified_time"),
                              "url": f.get("url"), "folder": " / ".join(path)})
        token = data.get("next_page_token") or data.get("page_token") if data.get("has_more") else ""
        if not token:
            return items


def wiki_items(sp):
    return [{"token": n["node_token"], "obj": n.get("obj_token"), "type": n.get("obj_type"), "title": n.get("title"),
             "created": n.get("obj_create_time"), "edited": n.get("obj_edit_time"), "url": n.get("url"),
             "folder": " / ".join([sp["name"], *path])} for n, path in walk(sp["space_id"])]


def clean(md):
    """飞书 markdown 里混着排版标签和要登录才能看的图片：去掉，只留文字。"""
    md = re.sub(r"(?s)<title>.*?</title>\s*", "", md)
    md = re.sub(r"(?m)^\s*!\[[^\]]*\]\([^)]*\)\s*$\n?", "", md)  # 整行图片
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)
    md = re.sub(r"<sub-page-list[^>]*>(</sub-page-list>)?", "", md)
    md = re.sub(r'<cite\b[^>]*?title="([^"]*)"[^>]*>(</cite>)?', r"《\1》", md)  # 引用的其他飞书文档
    md = re.sub(r'<bookmark\b[^>]*?name="([^"]*)"[^>]*?href="([^"]*)"[^>]*>(</bookmark>)?', r"[\1](\2)\n", md)
    md = re.sub(r"</?p>", "\n", md)
    md = re.sub(r"</?(grid|column|figure|source|callout|view|quote-container)[^>]*>", "", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def iso(ts):
    return datetime.fromtimestamp(int(ts)).isoformat() if str(ts or "").isdigit() else ""


MINUTES = re.compile(r"^(智能纪要|文字记录)[：:]\s*(.+)$")  # 飞书妙记转存到云空间后是这样两篇


def yaml_str(s):
    return json.dumps(str(s), ensure_ascii=False)


def main():
    p = kb_arg()
    p.add_argument("--only", choices=["wiki", "drive"], help="只导知识库或只导云空间")
    p.add_argument("--space", action="append", help="知识库只导这些 space_id；my_library 表示个人文档库")
    p.add_argument("--list-spaces", action="store_true")
    a = p.parse_args()
    out_dir = Path(a.kb) / "inbox" / "feishu"
    out_dir.mkdir(parents=True, exist_ok=True)

    groups = []
    if a.only != "drive":
        all_spaces = spaces()
        if a.list_spaces:
            for sp in all_spaces:
                print(f"{sp['space_id']}\t{sp['name']}")
            return
        if a.space:
            lib_id = all_spaces[0]["space_id"]
            wanted = {lib_id if x == "my_library" else x for x in a.space}
            all_spaces = [sp for sp in all_spaces if sp["space_id"] in wanted]
        groups += [(f"知识空间「{sp['name']}」", wiki_items(sp)) for sp in all_spaces]
    if a.only != "wiki":
        groups.append(("云空间", walk_drive()))

    seen, skipped, fetched, unchanged, failed, empty, files = set(), [], 0, 0, [], 0, 0
    for name, items in groups:
        print(f"{name}共 {len(items)} 个文件/节点", flush=True)
        for it in items:
            title = (it["title"] or "").strip() or "未命名"
            if it["obj"] in seen:  # 快捷方式、或同一文档既在知识库又在云空间
                continue
            seen.add(it["obj"])
            if it["type"] == "file" and title.lower().endswith(FILE_SUFFIXES):
                # 文件夹用 token 保证唯一，文件名带上传日期，import_files.py 从文件名取日期和标题
                dest = out_dir / "files" / it["token"] / f"{iso(it['created'])[:10]} {re.sub(r'[\\/:*?\"<>|]', '_', title)}"
                if dest.exists():
                    unchanged += 1
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                # lark-cli 只接受当前目录下的相对路径
                res = lark(["drive", "+download", "--file-token", it["token"], "--output", dest.name, "--overwrite"],
                           cwd=dest.parent)
                if res.get("ok") and dest.exists():
                    files += 1
                    print(f"  ✓ {title}", flush=True)
                else:
                    failed.append(f"{title}：{json.dumps(res.get('error'), ensure_ascii=False)[:150]}")
                continue
            if it["type"] not in ("docx", "doc"):
                skipped.append(f"{title}（{it['type']}）")
                continue
            m = MINUTES.match(title)
            if m:  # 纪要和逐字稿用同一个名字 + 后缀，import_files.py 会合成一条录音笔记
                title = m.group(2).strip()
                dest = out_dir / f"{re.sub(r'[\\/:*?\"<>|]', '_', title)}_{'纪要' if m.group(1) == '智能纪要' else '逐字稿'}.md"
            else:
                dest = out_dir / f"{it['token']}.md"
            edited = str(it["edited"] or "")
            if dest.exists() and f"edited: {edited}\n" in dest.read_text(encoding="utf-8")[:800]:
                unchanged += 1
                continue
            res = lark(["docs", "+fetch", "--doc", it["token"], "--doc-format", "markdown"])
            if not res.get("ok"):
                failed.append(f"{title}：{json.dumps(res.get('error'), ensure_ascii=False)[:150]}")
                continue
            body = clean(((res.get("data") or {}).get("document") or {}).get("content") or "")
            if len(re.sub(r"(?m)^#+ .*$|\s", "", body)) < 20:  # 只有标题、或只是引用别的空间的目录页
                empty += 1
                continue
            dest.write_text(
                "---\n"
                f"title: {yaml_str(title)}\n"
                f"created: {iso(it['created'])}\n"
                f"edited: {edited}\n"
                f"url: {it['url'] or ''}\n"
                + ("type: 飞书妙记\nis_audio: true\n" if m else "type: 飞书文档\n") +
                f"folder: {yaml_str(it['folder'])}\n"
                "---\n"
                f"> 飞书目录：{it['folder']}\n\n{body}\n",
                encoding="utf-8")
            fetched += 1
            print(f"  ✓ {title}", flush=True)

    print(f"新下载/更新 {fetched} 篇文档、{files} 个文件，未变化跳过 {unchanged} 篇，空文档 {empty} 篇（不导入）→ {out_dir}", flush=True)
    if skipped:
        print(f"非文档节点不导出 {len(skipped)} 个：{'、'.join(skipped[:15])}")
    if failed:
        print(f"下载失败 {len(failed)} 篇（重跑会重试）：")
        for f in failed[:20]:
            print("  ", f)
    sys.stdout.flush()
    subprocess.run([sys.executable, str(SCRIPTS / "import_files.py"), "--kb", a.kb, "--source", "feishu",
                    "--dir", str(out_dir)], check=True)


if __name__ == "__main__":
    main()
