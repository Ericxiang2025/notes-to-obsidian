"""只读导出腾讯 ima 的全部笔记和知识库里的文件，落成 md / 原文件，再交给 import_files.py。

  python3 fetch_ima.py --kb <KB>                 全部笔记 + 全部知识库
  python3 fetch_ima.py --kb <KB> --only notes    只导笔记；--only kb 只导知识库里的文件

凭证（和官方 ima-skill 共用）：环境变量 IMA_OPENAPI_CLIENTID / IMA_OPENAPI_APIKEY，
或 ~/.config/ima/client_id 和 ~/.config/ima/api_key。在 https://ima.qq.com/agent-interface 生成。
全程只调读接口（list_note / get_doc_content / search_knowledge_base / get_knowledge_list / get_media_info）。

输出：KB/inbox/ima/<note_id>.md               笔记（正文转成 Markdown，图片去掉）
     KB/inbox/ima/files/<媒体ID>/<原文件名>     知识库里的 PDF / Word / Markdown / TXT / HTML
笔记没改过（modify_time 不变）、文件已下载过就跳过；最后自动运行 import_files.py --source ima。
知识库里的笔记和「笔记」列表是同一篇，只导一次；网页、公众号文章、图片、录音、PPT、Excel 不导出，结尾列出来。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from common import kb_arg

SCRIPTS = Path(__file__).resolve().parent
BASE = "https://ima.qq.com/"
INTERVAL = 0.2
FILE_TYPES = {1: ".pdf", 3: ".docx", 7: ".md", 13: ".txt", 20: ".html"}  # media_type → 默认扩展名
TYPE_NAMES = {2: "网页", 4: "PPT", 5: "Excel", 6: "公众号文章", 9: "图片", 12: "AI会话", 14: "Xmind",
              15: "录音", 16: "视频", 21: "EPUB"}


def credentials():
    cid, key = os.environ.get("IMA_OPENAPI_CLIENTID"), os.environ.get("IMA_OPENAPI_APIKEY")
    d = Path.home() / ".config" / "ima"
    if not (cid and key) and (d / "client_id").exists() and (d / "api_key").exists():
        cid, key = (d / "client_id").read_text().strip(), (d / "api_key").read_text().strip()
    if not (cid and key):
        sys.exit("没有 ima 凭证：在 https://ima.qq.com/agent-interface 生成 Client ID 和 API Key，"
                 "存到 ~/.config/ima/client_id 和 ~/.config/ima/api_key")
    return {"ima-openapi-clientid": cid, "ima-openapi-apikey": key, "Content-Type": "application/json"}


HEADERS = None


def call(path, body):
    for attempt in range(3):
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=HEADERS, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.load(r)
            time.sleep(INTERVAL)
            return out
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            err = e
            time.sleep(2 ** attempt)
    return {"code": -1, "msg": f"网络错误：{err}"}


def must(out, what):
    if out.get("code") != 0:
        hint = "（凭证不对或已失效，去 ima.qq.com/agent-interface 重新生成）" if out.get("code") in (100002, 100003, 401) else ""
        sys.exit(f"{what}失败：{out.get('code')} {out.get('msg')}{hint}")
    return out.get("data") or {}


# ---------- 笔记 JSON → Markdown ----------
def inline(nodes):
    out = []
    for n in nodes or []:
        if "text" in n:
            t = re.sub(r"[\x00-\x08\x0b-\x1f]", "", n["text"])  # ima 无标题笔记的标题块是一个退格符
            out.append(f"**{t}**" if n.get("bold") and t.strip() else t)
        elif n.get("type") in ("link", "a"):
            out.append(f"[{inline(n.get('children'))}]({n.get('url', '')})")
        else:
            out.append(inline(n.get("children")))
    return "".join(out)


def blocks(nodes, depth=0):
    lines = []
    for n in nodes or []:
        t = n.get("type", "")
        if t in ("cloud_image", "image", "img"):  # 图片链接带签名、会过期
            continue
        if re.fullmatch(r"h[1-6]", t):
            text = inline(n.get("children")).strip()
            if len(text) > 60:  # 没写标题时 ima 把整段正文塞进标题块，当普通段落
                lines.append(text)
            elif text:
                lines.append("#" * int(t[1]) + " " + text)
        elif t == "table":
            rows = [[inline(td.get("children")).replace("|", "／").replace("\n", " ").strip()
                     for td in tr.get("children") or []] for tr in n.get("children") or []]
            rows = [r for r in rows if any(r)]
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [""] * (width - len(r)) for r in rows]
                table = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
                table += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                lines.append("\n".join(table))  # 表格行之间不能有空行
        elif t in ("ul", "ol", "bulleted-list", "numbered-list"):
            for i, li in enumerate(n.get("children") or [], 1):
                mark = f"{i}." if t in ("ol", "numbered-list") else "-"
                sub = blocks(li.get("children"), depth + 1) if any("type" in c for c in li.get("children") or []) \
                    else [inline(li.get("children"))]
                if sub:
                    lines.append("  " * depth + f"{mark} {sub[0].strip()}")
                    lines += ["  " * (depth + 1) + s for s in sub[1:]]
        elif t in ("code_block", "code"):
            lines += ["```", "\n".join(inline([c]) for c in n.get("children") or []), "```"]
        elif t in ("blockquote", "quote"):
            lines += ["> " + s for s in blocks(n.get("children"), depth) or [inline(n.get("children"))]]
        elif any("type" in c for c in n.get("children") or []) and t not in ("p", "li", "lic"):
            lines += blocks(n.get("children"), depth)
        else:
            text = inline(n.get("children")).rstrip()
            if text.strip():
                lines.append(text)
    return lines


def note_markdown(note_id):
    out = call("openapi/note/v1/get_doc_content", {"note_id": note_id, "target_content_format": 2})
    if out.get("code") != 0:
        return None, f"{out.get('code')} {out.get('msg')}"
    raw = (out.get("data") or {}).get("content") or ""
    try:
        return "\n\n".join(blocks(json.loads(raw))), ""
    except (json.JSONDecodeError, TypeError, AttributeError):
        plain = call("openapi/note/v1/get_doc_content", {"note_id": note_id, "target_content_format": 0})
        return ((plain.get("data") or {}).get("content") or ""), ""


# ---------- 列表 ----------
def list_notes():
    notes = []
    while True:
        data = must(call("openapi/note/v1/list_note", {"cursor": str(len(notes)), "limit": 20}), "列笔记")
        page = data.get("note_book_list") or []
        notes += page
        if data.get("is_end") or not page:
            return notes


def list_kbs():
    kbs, cursor = [], ""
    while True:
        data = must(call("openapi/wiki/v1/search_knowledge_base", {"query": "", "cursor": cursor, "limit": 20}), "列知识库")
        kbs += data.get("info_list") or []
        cursor = data.get("next_cursor") or ""
        if data.get("is_end") or not cursor:
            return kbs


def list_kb_items(kb_id, folder="", path=()):
    items, cursor = [], ""
    while True:
        body = {"cursor": cursor, "limit": 50, "knowledge_base_id": kb_id}
        if folder:
            body["folder_id"] = folder
        data = must(call("openapi/wiki/v1/get_knowledge_list", body), "列知识库内容")
        for it in data.get("knowledge_list") or []:
            if it.get("media_id"):
                items.append((it, path))
            elif it.get("folder_id"):  # 子文件夹
                items += list_kb_items(kb_id, it["folder_id"], path + (it.get("name") or "",))
        cursor = data.get("next_cursor") or ""
        if data.get("is_end") or not cursor:
            return items


def iso_ms(ms):
    return datetime.fromtimestamp(int(ms) / 1000).replace(microsecond=0).isoformat() if str(ms or "").isdigit() else ""


def safe(name):
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip() or "未命名"


def yaml_str(s):
    return json.dumps(str(s), ensure_ascii=False)


def main():
    global HEADERS
    p = kb_arg()
    p.add_argument("--only", choices=["notes", "kb"], help="只导笔记或只导知识库文件")
    a = p.parse_args()
    HEADERS = credentials()
    out_dir = Path(a.kb) / "inbox" / "ima"
    out_dir.mkdir(parents=True, exist_ok=True)

    note_kbs, files, skipped = {}, [], []
    if a.only != "notes":
        kbs = list_kbs()
        print(f"知识库 {len(kbs)} 个：{'、'.join(k['kb_name'] for k in kbs)}", flush=True)
        for kb in kbs:
            for it, path in list_kb_items(kb["kb_id"]):
                folder = " / ".join(["ima知识库", kb["kb_name"], *path])
                mt = it.get("media_type")
                if mt == 11:  # 知识库里的笔记：记下归属，正文在笔记那边导
                    info = must(call("openapi/wiki/v1/get_media_info", {"media_id": it["media_id"]}), "读媒体信息")
                    nid = (info.get("notebook_ext_info") or {}).get("notebook_id")
                    if nid:
                        note_kbs.setdefault(nid, []).append(kb["kb_name"])
                elif mt in FILE_TYPES:
                    files.append((it, folder))
                else:
                    skipped.append(f"{it.get('title') or '未命名'}（{TYPE_NAMES.get(mt, mt)}）")

    fetched = unchanged = empty = got_files = 0
    failed = []
    if a.only != "kb":
        notes = list_notes()
        print(f"笔记 {len(notes)} 篇", flush=True)
        for n in notes:
            nid = n["note_id"]
            title = re.sub(r"[\x00-\x1f]", "", n.get("title") or "").strip()
            if not title:  # 没写标题的笔记，用正文开头当标题
                title = re.split(r"[。！？!?\n]", (n.get("summary") or "").strip())[0][:30] or "未命名笔记"
            dest = out_dir / f"{nid}.md"
            edited = str(n.get("modify_time") or "")
            if dest.exists() and f"edited: {edited}\n" in dest.read_text(encoding="utf-8")[:800]:
                unchanged += 1
                continue
            body, err = note_markdown(nid)
            if body is None:
                failed.append(f"{title}：{err}")
                continue
            if len(re.sub(r"\s", "", body)) < 10:
                empty += 1
                continue
            folder = (n.get("note_ext_info") or {}).get("folder_name") or "ima笔记"
            in_kbs = note_kbs.get(nid, [])
            dest.write_text(
                "---\n"
                f"title: {yaml_str(title)}\n"
                f"created: {iso_ms(n.get('create_time'))}\n"
                f"edited: {edited}\n"
                "type: ima笔记\n"
                f"folder: {yaml_str(folder)}\n"
                "---\n"
                f"> ima 笔记本：{folder}" + (f"　所在知识库：{'、'.join(in_kbs)}" if in_kbs else "") + f"\n\n{body}\n",
                encoding="utf-8")
            fetched += 1
            print(f"  ✓ {title}", flush=True)

    for it, _ in files:
        title = safe(it.get("title") or "未命名")
        if not Path(title).suffix:
            title += FILE_TYPES[it["media_type"]]
        sub = hashlib.sha1(it["media_id"].encode()).hexdigest()[:12]
        dest = out_dir / "files" / sub / title
        if dest.exists():
            unchanged += 1
            continue
        info = call("openapi/wiki/v1/get_media_info", {"media_id": it["media_id"]})
        url_info = (info.get("data") or {}).get("url_info") or {}
        if info.get("code") != 0 or not str(url_info.get("url", "")).startswith("https://"):
            failed.append(f"{title}：拿不到下载链接，只能在 ima 客户端里看")
            continue
        req = urllib.request.Request(url_info["url"], headers=url_info.get("headers") or {})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            failed.append(f"{title}：下载失败 {e}")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        got_files += 1
        print(f"  ✓ {title}", flush=True)

    print(f"新下载/更新 {fetched} 篇笔记、{got_files} 个文件，未变化跳过 {unchanged} 个，空笔记 {empty} 篇（不导入）→ {out_dir}",
          flush=True)
    if skipped:
        print(f"不导出 {len(skipped)} 个：{'、'.join(skipped[:15])}")
    if failed:
        print(f"失败 {len(failed)} 个（重跑会重试）：")
        for f in failed[:20]:
            print("  ", f)
    sys.stdout.flush()
    subprocess.run([sys.executable, str(SCRIPTS / "import_files.py"), "--kb", a.kb, "--source", "ima",
                    "--dir", str(out_dir)], check=True)


if __name__ == "__main__":
    main()
