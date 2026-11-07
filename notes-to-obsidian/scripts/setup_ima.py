"""让用户自己把 ima 的 Client ID 和 API Key 存到本机，agent 全程看不到明文。

  python3 setup_ima.py            弹两个输入框（macOS）或在终端里输入，存到 ~/.config/ima/，然后测一次连接
  python3 setup_ima.py --check    只测连接

先请用户打开 https://ima.qq.com/agent-interface 生成凭证。两个值粘反了会自动纠正（Client ID 是 32 位十六进制）。
"""
import getpass
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

DIR = Path.home() / ".config" / "ima"


def ask(label, step):
    if sys.platform == "darwin":
        script = (f'text returned of (display dialog "第 {step}/2：请粘贴 ima 的 {label}\\n只粘贴这一项，粘完点「保存」。" '
                  'default answer "" with title "连接腾讯 ima" buttons {"取消", "保存"} default button "保存" giving up after 900)')
        p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return p.stdout.strip()
    return getpass.getpass(f"请粘贴 ima 的 {label}（输入时不显示），回车确认：").strip()


def check():
    cid, key = (DIR / "client_id").read_text().strip(), (DIR / "api_key").read_text().strip()
    req = urllib.request.Request(
        "https://ima.qq.com/openapi/wiki/v1/search_knowledge_base",
        data=json.dumps({"query": "", "cursor": "", "limit": 20}).encode(), method="POST",
        headers={"ima-openapi-clientid": cid, "ima-openapi-apikey": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.load(r)
    if out.get("code") != 0:
        sys.exit(f"连接失败：{out.get('code')} {out.get('msg')}——请在 ima.qq.com/agent-interface 重新生成 API Key 后再运行本脚本")
    kbs = (out.get("data") or {}).get("info_list") or []
    print(f"✅ ima 已连接，能看到 {len(kbs)} 个知识库：{'、'.join(k['kb_name'] for k in kbs)}")


def main():
    if "--check" not in sys.argv:
        cid = re.sub(r"\s", "", ask("Client ID", 1))
        key = re.sub(r"\s", "", ask("API Key", 2))
        if not cid or not key:
            sys.exit("没有收到两个值，已取消")
        if not re.fullmatch(r"[0-9a-f]{32}", cid) and re.fullmatch(r"[0-9a-f]{32}", key):
            cid, key = key, cid
            print("两个值粘反了，已自动对调")
        DIR.mkdir(parents=True, exist_ok=True)
        DIR.chmod(0o700)
        for name, value in (("client_id", cid), ("api_key", key)):
            (DIR / name).write_text(value)
            (DIR / name).chmod(0o600)
        print(f"已保存到 {DIR}（只有你的账户能读）")
    if not (DIR / "client_id").exists():
        sys.exit("还没有凭证：先不带参数运行本脚本")
    check()


if __name__ == "__main__":
    main()
