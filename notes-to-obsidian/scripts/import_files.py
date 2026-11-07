"""把一个文件夹里导出的笔记/录音转写，转成统一格式笔记。适用于飞书、ima、录音卡、Notion 等一切「先导出成文件」的来源。

  python3 import_files.py --kb <KB> --source plaud --dir <导出文件夹>
  python3 import_files.py --kb <KB> --source myapp --label "我的App" --dir <文件夹>   # 不在 SOURCES 表里的来源
  python3 import_files.py --kb <KB> --source plaud --dir <文件夹> --audio            # 这个文件夹全是录音转写

支持：.md .markdown .txt .docx .pdf .srt .vtt .html .htm；.zip 会先解压到同目录再导入。
每次运行都重新扫描整个文件夹并重写 normalized/<来源>.jsonl（文件不动 → note_id 不变，已分类、已进 Obsidian 的不会重复）。

元数据取法（按优先级）：
  标题  md 的 YAML front matter title → 第一个 # 标题 → 文件名（去掉日期、「_原文」等后缀）
  时间  front matter created/date → 文件名里的日期 → 正文前 300 字里的日期 → 文件修改时间（会提示是推测）
  录音  .srt/.vtt、--audio、front matter is_audio: true、或正文里有 5 行以上时间戳 → 当作逐字稿
配对：同一目录下同名、只差「_原文/_转写/_逐字稿/_摘要/_纪要/_summary/_transcript」后缀的文件合成一条笔记，
     摘要类进正文，原文类进逐字稿（Plaud、通义听悟、钉钉导出都会把摘要和原文拆成两个文件）。
"""
import hashlib
import html
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from common import SOURCES, kb_arg, note_record, write_normalized

TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".docx", ".pdf", ".srt", ".vtt", ".html", ".htm"}
TRANSCRIPT_TAGS = ["原文", "转写", "转写稿", "逐字稿", "文字记录", "transcript", "Transcript"]
SUMMARY_TAGS = ["摘要", "纪要", "总结", "笔记", "summary", "Summary", "notes", "Notes"]
TAG_RE = re.compile(r"[\s_\-－—（(]+(" + "|".join(map(re.escape, TRANSCRIPT_TAGS + SUMMARY_TAGS)) + r")[)）]?$")
DATE_RES = [
    re.compile(r"(20\d{2})[-_./年](\d{1,2})[-_./月](\d{1,2})日?(?:[ T_\-]?(\d{1,2})[:：.\-](\d{2}))?"),
    re.compile(r"(20\d{2})(\d{2})(\d{2})(?:[_\-T ]?(\d{2})(\d{2}))?"),
]
TS_LINE = re.compile(r"^\s*(\[?\d{1,2}:\d{2}(?::\d{2})?\]?|发言人\s*\d+|说话人\s*\d+|Speaker\s*\d+)", re.M)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


# ---------- 读各种格式 ----------
def read_docx(path):
    with zipfile.ZipFile(path) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    lines = []
    for p in root.iter(f"{W}p"):
        text = "".join(t.text or "" for t in p.iter(f"{W}t"))
        style = p.find(f"{W}pPr/{W}pStyle")
        level = re.search(r"(\d)", style.get(f"{W}val", "")) if style is not None and "eading" in style.get(f"{W}val", "") else None
        if text.strip():
            lines.append(("#" * int(level.group(1)) + " " if level else "") + text)
    return "\n".join(lines)


def read_pdf(path):
    if shutil.which("pdftotext"):
        p = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout
    try:
        from pypdf import PdfReader  # 可选依赖：pip install pypdf
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    except ImportError:
        return None


def read_subtitles(text):
    """srt/vtt → 纯文本逐字稿：去序号、时间轴、WEBVTT 头和 <v 说话人> 标签，合并相邻重复行。"""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.isdigit() or "-->" in line or line.startswith(("WEBVTT", "NOTE", "Kind:", "Language:")):
            continue
        m = re.match(r"<v\s+([^>]+)>(.*)", line)
        if m:
            line = f"{m.group(1)}：{m.group(2)}"
        line = re.sub(r"</?[^>]+>", "", line)
        if not out or out[-1] != line:
            out.append(line)
    return "\n".join(out)


def read_html(text):
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def read_any(path):
    suf = path.suffix.lower()
    if suf == ".docx":
        return read_docx(path)
    if suf == ".pdf":
        return read_pdf(path)
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "gb18030", "utf-16"):  # 国内工具导出的 txt 常是 GBK
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="ignore")
    if suf in (".srt", ".vtt"):
        return read_subtitles(text)
    if suf in (".html", ".htm"):
        return read_html(text)
    return text


# ---------- 元数据 ----------
def front_matter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(":")
        if sep and not line.startswith((" ", "\t", "-")):
            meta[k.strip()] = v.strip().strip("\"'")
    return meta, text[m.end():]


def find_date(text):
    for r in DATE_RES:
        m = r.search(text or "")
        if m:
            y, mo, d, h, mi = (m.groups() + (None, None))[:5]
            try:
                return datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0)).isoformat()
            except ValueError:
                continue
    return None


def clean_title(stem):
    t = TAG_RE.sub("", stem)
    for r in DATE_RES:
        t = r.sub("", t)
    return re.sub(r"^[\s_\-－—.]+|[\s_\-－—.]+$", "", t) or stem


def kind_of(stem):
    m = TAG_RE.search(stem)
    if not m:
        return None
    return "transcript" if m.group(1) in TRANSCRIPT_TAGS else "summary"


# ---------- 主流程 ----------
def unzip_all(folder):
    for z in folder.rglob("*.zip"):
        dest = z.with_suffix("")
        if not dest.exists():
            with zipfile.ZipFile(z) as f:
                f.extractall(dest)
            print(f"已解压 {z.name} → {dest.name}/")


def main():
    p = kb_arg()
    p.add_argument("--dir", required=True, help="导出文件所在文件夹")
    p.add_argument("--source", required=True, help="来源 key，如 feishu / ima / plaud / dingtalk / tingwu / notion / files")
    p.add_argument("--label", help="来源显示名；source 不在内置表里时必填")
    p.add_argument("--audio", action="store_true", help="文件夹里全是录音转写")
    a = p.parse_args()
    folder = Path(a.dir).expanduser().resolve()
    if not folder.is_dir():
        sys.exit(f"文件夹不存在：{folder}")
    if not re.fullmatch(r"[a-z0-9_]+", a.source):
        sys.exit("--source 只能用小写字母、数字、下划线")
    label = a.label or SOURCES.get(a.source)
    if not label:
        sys.exit(f"来源 {a.source} 不在内置表里，请加 --label 显示名")
    unzip_all(folder)

    groups, skipped = {}, []
    for f in sorted(folder.rglob("*")):
        if not f.is_file() or f.name.startswith((".", "~$")) or f.suffix.lower() not in TEXT_SUFFIXES:
            if f.is_file() and not f.name.startswith(".") and f.suffix.lower() not in TEXT_SUFFIXES | {".zip"}:
                skipped.append(f.name)
            continue
        key = (f.parent, clean_title(f.stem).lower())
        groups.setdefault(key, []).append(f)

    records, unreadable, guessed = [], [], 0
    for (parent, _), files in groups.items():
        content, transcript, meta, first = [], [], {}, files[0]
        for f in files:
            text = read_any(f)
            if text is None:
                unreadable.append(f.name)
                continue
            m, body = front_matter(text)
            meta = {**m, **meta}
            body = body.strip()
            if not body:
                continue
            kind = kind_of(f.stem)
            looks_audio = len(TS_LINE.findall(body)) >= 5
            if f.suffix.lower() in (".srt", ".vtt") or kind == "transcript" or (kind is None and looks_audio and len(files) > 1):
                transcript.append(body)
            elif kind is None and (a.audio or meta.get("is_audio") == "true" or looks_audio):
                transcript.append(body)
            else:
                content.append(body)
        if not content and not transcript:
            continue
        joined = "\n\n".join(content)
        heading = re.search(r"^#\s+(.+)$", joined, re.M)
        title = meta.get("title") or (heading.group(1).strip() if heading and len(files) == 1 else None) or clean_title(first.stem)
        created = (find_date(meta.get("created", "") or meta.get("date", "")) or find_date(first.stem)
                   or find_date((joined or "\n".join(transcript))[:300]))
        if not created:
            created = datetime.fromtimestamp(min(f.stat().st_mtime for f in files)).replace(microsecond=0).isoformat()
            guessed += 1
        rel = str(first.relative_to(folder).with_name(clean_title(first.stem)))
        is_audio = bool(transcript) or a.audio or meta.get("is_audio") == "true"
        records.append(note_record(
            note_id=f"{a.source}-{hashlib.sha1(rel.encode('utf-8')).hexdigest()[:12]}",
            source=a.source, source_label=label, title=title[:80],
            type_label=meta.get("type") or ("录音转写" if is_audio else f"{label}文档"),
            is_audio=is_audio, created_at=created,
            note_url=meta.get("url") or meta.get("source_url") or "",
            duration_min=int(meta["duration_min"]) if str(meta.get("duration_min", "")).isdigit() else None,
            content=joined, transcript="\n\n".join(transcript),
            origin_path=str(first),
        ))

    out = write_normalized(a.kb, a.source, records)
    print(f"导入 {len(records)} 条（{label}）→ {out}；其中录音转写 {sum(r['is_audio'] for r in records)} 条")
    if guessed:
        print(f"有 {guessed} 条找不到日期，用了文件修改时间（可能是导出那天，不是录音那天）")
    if unreadable:
        print(f"读不出文字 {len(unreadable)} 个（扫描版 PDF 或缺 pdftotext/pypdf）：{unreadable[:10]}")
    if skipped:
        print(f"跳过不支持的格式 {len(skipped)} 个：{skipped[:10]}")


if __name__ == "__main__":
    main()
