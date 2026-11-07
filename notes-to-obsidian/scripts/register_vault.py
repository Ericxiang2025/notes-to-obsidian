"""把仓库登记到 Obsidian 的仓库列表，并设为下次启动时打开。

  python3 register_vault.py --kb <KB>

为什么需要：仓库没登记时，obsidian:// 链接和 open 命令都不会打开它，首次启动只会弹出选择仓库的窗口，
而那个文件选择框很难自动操作。登记后直接启动 Obsidian 就会打开这个仓库。

必须先完全退出 Obsidian：它退出时会用内存里的列表覆盖 obsidian.json，运行中改的内容会丢。
只增改本仓库这一项，保留用户已有的其他仓库。
"""
import json
import os
import platform
import secrets
import subprocess
import sys
import time
from pathlib import Path

from common import kb_arg, load_config


def config_path():
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library/Application Support/obsidian/obsidian.json"
    if system == "Windows":
        return Path(os.environ["APPDATA"]) / "obsidian" / "obsidian.json"
    return Path.home() / ".config/obsidian/obsidian.json"


def obsidian_running():
    system = platform.system()
    if system == "Windows":
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Obsidian.exe"], capture_output=True, text=True).stdout
        return "Obsidian.exe" in out
    name = "Obsidian" if system == "Darwin" else "obsidian"
    return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0


def main():
    cfg = load_config(kb_arg().parse_args().kb)
    vault = str(Path(cfg["vault"]).expanduser().resolve())
    if not Path(vault).is_dir():
        sys.exit(f"仓库目录不存在：{vault}。先运行 build_obsidian.py")
    if obsidian_running():
        sys.exit("Obsidian 正在运行：先完全退出（macOS 用 Cmd+Q，或 osascript -e 'quit app \"Obsidian\"'），再重跑本脚本")

    path = config_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    vaults = data.setdefault("vaults", {})
    vid = next((k for k, v in vaults.items() if v.get("path") == vault), None) or secrets.token_hex(8)
    for v in vaults.values():
        v.pop("open", None)
    vaults[vid] = {"path": vault, "ts": int(time.time() * 1000), "open": True}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"已登记仓库 {vault}（id={vid}），现在启动 Obsidian 会直接打开它")


if __name__ == "__main__":
    main()
