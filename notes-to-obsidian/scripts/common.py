"""各脚本共用：工作目录、配置、分类表、来源、统一笔记格式。只用标准库。

工作目录（下文称 KB）由 --kb 指定，结构：
  KB/config.json              用户配置（见 load_config）
  KB/raw/<来源>/              各平台原始数据（得到大脑的 JSON、用户导出的文件不复制，只记路径）
  KB/normalized/<来源>.jsonl  各导入脚本输出的统一格式笔记（见 NOTE_FIELDS）
  KB/work/                    分类规范与分类结果
  KB/知识库/ 逐字稿/ 索引/ kb.sqlite README.md   build_kb.py 生成
"""
import argparse
import json
import re
import sys
from pathlib import Path

# 双轨分类：企业提效 + 自媒体共用一套代码。单选「企业」或「自媒体」时不删分类，
# 只是对应的看板和引导不同；没有笔记的分类不在 Obsidian 首页显示。
CATEGORIES = {
    "01_客户沟通": "与客户/潜在客户的需求访谈、咨询、回访、成交沟通",
    "02_合作洽谈": "与外部机构/企业/个人谈合作、渠道、报价、合同、商单",
    "03_内部会议与复盘": "团队或合伙人内部会议、复盘、分工、财务",
    "04_方法与SOP": "可复用的方法、流程、方案、课件、操作手册、培训交付",
    "05_学习输入": "听课、峰会、读书、他人分享、文章摘录",
    "06_选题素材": "可以做成内容的观点、故事、案例、金句、闪念",
    "07_内容作品": "已写好的口播稿、文案、脚本，及自媒体运营方法",
    "08_AI工具与提示词": "提示词、智能体设定、AI 工具用法与资讯",
    "09_个人思考": "随笔、感悟、个人规划、备忘",
    "10_生活私事": "与工作无关的日常与私人事务",
    "99_空白无效": "空录音、测试、无实际内容",
}
UNCLASSIFIED = "00_待分类"
USES = ["企业提效", "自媒体素材"]
POTENTIAL = ["高", "中", "低"]
MODES = {"dual": "企业提效 + 自媒体", "enterprise": "企业提效", "media": "自媒体"}

# 来源 key → 显示名。导入脚本的 --source 用 key；不在表里的 key 直接用 --label 作显示名
SOURCES = {
    "getnote": "得到大脑", "ima": "腾讯ima", "feishu": "飞书", "feishu_minutes": "飞书妙记",
    "plaud": "Plaud录音卡", "dingtalk": "钉钉闪记", "iflytek": "讯飞", "tingwu": "通义听悟",
    "notion": "Notion", "yinxiang": "印象笔记", "youdao": "有道云笔记", "files": "本地文件",
}
# 统一笔记格式：每个导入脚本都输出这些字段，后面的分类、建库、Obsidian 只认这个格式
NOTE_FIELDS = {
    "note_id": "全局唯一；得到大脑用原 ID，其他来源用「来源-稳定哈希」",
    "source": "来源 key", "source_label": "来源显示名",
    "title": "标题", "type_label": "类型，如 会议录音 / 文字笔记 / 飞书文档",
    "is_audio": "是否录音类", "created_at": "ISO 时间", "date": "YYYY-MM-DD",
    "note_url": "原笔记链接，没有填空串", "web_url": "原网页链接或 None",
    "duration_min": "录音分钟数或 None", "source_tags": "原平台标签列表",
    "content": "正文 / AI 总结（Markdown）", "transcript": "逐字稿全文，没有填空串",
    "chapters": "[{start_ms, title}]", "todos": "[{text, completed}]",
    "origin_path": "本地导入文件的绝对路径，API 来源填空串",
}


def note_record(**kw):
    """补齐缺省字段并校验，导入脚本都用它生成记录。"""
    rec = {"web_url": None, "duration_min": None, "source_tags": [], "content": "", "transcript": "",
           "chapters": [], "todos": [], "note_url": "", "is_audio": False, "origin_path": ""}
    rec.update(kw)
    rec.setdefault("source_label", SOURCES.get(rec.get("source"), rec.get("source")))
    rec["date"] = str(rec.get("created_at") or "")[:10]
    missing = [k for k in NOTE_FIELDS if k not in rec or (k in ("note_id", "title", "created_at") and not rec[k])]
    if missing:
        raise ValueError(f"笔记缺少字段 {missing}")
    return rec


def write_normalized(kb, source, records):
    out = Path(kb) / "normalized" / f"{source}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return out


def load_normalized(kb):
    """读全部来源的统一格式笔记，按 note_id 去重（同一 ID 以后读到的为准）。"""
    recs = {}
    for f in sorted((Path(kb) / "normalized").glob("*.jsonl")):
        for r in read_jsonl(f):
            recs[r["note_id"]] = r
    return list(recs.values())


TYPE_LABEL = {
    "recorder_audio": "录音卡长录音", "recorder_flash_audio": "录音卡闪念", "meeting": "会议录音",
    "audio": "即时录音", "local_audio": "上传音频", "class_audio": "课堂录音",
    "internal_record": "系统内录", "plain_text": "文字笔记", "link": "链接笔记", "img_text": "图片笔记",
}
# getnote 中有逐字稿接口的笔记类型（来自 getnote note --help 与实测）
AUDIO_TYPES = {
    "recorder_audio", "recorder_flash_audio", "meeting", "audio",
    "local_audio", "class_audio", "internal_record",
}
IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def kb_arg(parser=None):
    parser = parser or argparse.ArgumentParser()
    parser.add_argument("--kb", required=True, help="知识库工作目录（含 config.json）")
    return parser


def load_config(kb):
    """config.json 字段：
    owner       称呼，如「王老师」（必填）
    kb_name     知识库名，如「王老师的知识库」（必填，也是 Obsidian 仓库名）
    mode        dual / enterprise / media
    vault       Obsidian 仓库绝对路径
    self_names  本人的各种写法，人物列表里会去掉
    people_alias / org_alias  同一对象不同写法 → 统一名称
    """
    path = Path(kb) / "config.json"
    if not path.exists():
        sys.exit(f"缺少 {path}：先按 SKILL.md 第三步创建 config.json")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in ("owner", "kb_name", "mode", "vault") if not cfg.get(k)]
    if missing:
        sys.exit(f"config.json 缺少字段 {missing}")
    if cfg["mode"] not in MODES:
        sys.exit(f"config.json 的 mode 只能是 {list(MODES)}")
    cfg.setdefault("self_names", [])
    cfg.setdefault("people_alias", {})
    cfg.setdefault("org_alias", {})
    return cfg


def safe_name(text, limit=40):
    text = re.sub(r"[\\/:*?\"<>|#\[\]\n\r\t^]", " ", text or "未命名")
    text = re.sub(r"\s+", " ", text).strip(" .") or "未命名"
    return text[:limit].strip()


def ms_to_clock(ms):
    s = int(ms or 0) // 1000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def read_jsonl(path):
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
