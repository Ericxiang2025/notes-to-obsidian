# 各来源怎么把笔记拿到本地

原则：**所有来源先变成文件，再统一导入**。得到大脑有现成脚本；其他来源要么由你（agent）调官方 CLI/API 导出成 md，要么请用户手动导出，然后都交给 `import_files.py`。

标注说明：✅ 已实测　⚠️ 按官方文档写，未实测（第一次做时先拿 3-5 条试，确认格式对了再全量）

## 目录

- 落地格式（agent 自动导出时照这个写）
- 得到大脑 ✅
- 飞书云文档 / 知识库 / 妙记 ⚠️
- 腾讯 ima ⚠️
- Plaud 等录音卡
- 钉钉闪记、通义听悟、讯飞听见
- Notion、印象笔记、有道云笔记、Apple 备忘录、其他
- 导入命令和常见问题

## 落地格式

用 API/CLI 导出时，每条笔记写一个 `.md` 到 `KB/inbox/<来源key>/`，文件名用 `日期_标题.md`，开头写 front matter：

```markdown
---
title: 与李姐的视频号定位沟通
created: 2026-10-12T14:30:00
url: https://原平台的笔记链接
type: 飞书妙记
is_audio: true
duration_min: 42
---
这里放 AI 总结或正文（Markdown）
```

录音的逐字稿另存一个同名加 `_逐字稿` 后缀的文件（`2026-10-12_与李姐的视频号定位沟通_逐字稿.txt`），import_files.py 会自动把两份合成一条笔记。

文件名要稳定：同一条笔记每次导出都用同一个文件名，重跑才不会重复。标题里的 `/ \ : * ? " < > |` 换成 `_`。

## 得到大脑 ✅

全自动，见 SKILL.md 第三步。只读授权，不会新建或改动笔记。

## 飞书 ⚠️

用飞书官方命令行 lark-cli（github.com/larksuite/cli，MIT 开源）：

1. `npx @larksuite/cli@latest install`（需要 Node 18+）
2. `lark-cli config init --new`：按提示在飞书开放平台建一个自建应用。**企业版飞书可能要管理员审批**，卡住就请用户找管理员，或改走手动导出。
3. `lark-cli auth login --recommend`，浏览器里点授权；`lark-cli auth status` 确认已登录。
4. 所有命令都加 `--as user`，否则是用机器人身份，看不到用户自己的文档。

导出：
- 妙记（会议录音）：`lark-cli minutes +search --as user` 列出妙记，逐条 `minutes +detail` 取标题、时间、AI 纪要，逐字稿用 `minutes +download` 或开放接口 `GET /open-apis/minutes/v1/minutes/:minute_token/transcript`（权限 `minutes:minutes.transcript:export`，可选 txt/srt）。来源 key 用 `feishu_minutes`。
  - 返回 403：妙记所有者关掉了导出，只能请所有者打开或跳过。
- 云文档 / 知识库：`wiki +space-list` 列知识空间，`wiki +node-list` 列节点（**不递归**，要自己逐层往下列），`docs +fetch` 取文档正文。来源 key 用 `feishu`。

手动兜底：
- 妙记：打开妙记 → 右上角「···」→「导出文字记录」→ 选 TXT 或 SRT
- 文档：右上角「···」→「下载为」→ Word 或 PDF（只能一篇一篇导）

## 腾讯 ima ⚠️

ima 有官方 OpenAPI：

1. 请用户打开 https://ima.qq.com/agent-interface （或客户端：头像 →「Claw 配置」），生成 Client ID 和 API Key。**API Key 只显示一次**，让用户自己保存。
2. 不要让用户把 Key 发到聊天里。请用户自己在终端运行下面的命令，把 Key 存进本机文件（macOS；Windows 请用户用记事本新建这两个文件，各粘贴一行）：
   ```bash
   mkdir -p ~/.config/ima && pbpaste > ~/.config/ima/client_id   # 先复制 Client ID 再运行
   pbpaste > ~/.config/ima/api_key                               # 再复制 API Key 再运行
   ```
3. 请求一律 `POST https://ima.qq.com/openapi/...`，请求头带 `ima-openapi-clientid` 和 `ima-openapi-apikey`。
   - 笔记：`/openapi/note/v1/list_note_folder_by_cursor` 列笔记本，`search_note_book` 列笔记，`get_doc_content`（`target_content_format=0` 取纯文本）取正文。来源 key 用 `ima`。
   - 知识库：`/openapi/wiki/v1/search_knowledge_base`（query 传空串列出全部），`get_knowledge_list` 分页列条目（每页 1-50），`get_media_info` 取内容。
   - 官方 Skill 包里有完整接口说明：https://app-dl.ima.qq.com/skills/ima-skills-1.1.10.zip

手动兜底：ima 没有批量导出，只能单篇「导出 PDF」。笔记多时优先走 API。

## Plaud 等录音卡

- **Plaud**：App 或 web.plaud.ai 里打开录音 →「导出」，转写选 TXT 或 SRT，总结选 Markdown 或 DOCX。总结和转写是**两个文件**，都导出、放在同一个文件夹，不要改文件名后缀。
  - Plaud 也有官方 MCP / CLI（docs.plaud.ai/plaud-mcp-cli），浏览器登录授权，可以让你批量导出 ⚠️。来源 key 用 `plaud`。
- **其他录音卡（钉钉 A1、讯飞、科大讯飞录音笔等）**：只要能导出 TXT / SRT / DOCX 就行，来源 key 用它所属的平台，或用 `files` 加 `--label "XX录音卡"`。

## 钉钉闪记、通义听悟、讯飞听见

- **钉钉闪记 / AI 听记**：打开记录 →「…」→ 导出为 docx / PDF / srt（也能「保存到钉钉文档」再下载）。官方另有 `dws minutes` 命令行 ⚠️。来源 key `dingtalk`。
- **通义听悟**：记录列表里可批量勾选导出，原文文件会带「_原文」后缀，正好被自动配对。来源 key `tingwu`。
- **讯飞听见**：导出 doc / docx / txt / srt。来源 key `iflytek`。

## Notion、印象笔记、有道云笔记、Apple 备忘录、其他

- **Notion**：Settings →「导出全部工作区内容」→ 格式选 Markdown & CSV，下载得到 zip。zip 直接放进文件夹，脚本会自动解压。来源 key `notion`。
- **印象笔记**：导出 ENEX，再用第三方开源工具 Yarle 或 evernote2md 转成 Markdown 文件夹。来源 key `yinxiang`。
- **有道云笔记**：没有官方批量导出，只能单篇导出 Word/PDF。来源 key `youdao`。
- **Apple 备忘录**：iOS 26 / macOS 26 起可以单篇「导出为 Markdown」。来源 key `files` 加 `--label "Apple备忘录"`。
- **一堆 Word / PDF / TXT**：都放进一个文件夹，来源 key `files`。

## 导入命令

```bash
python3 SKILL_DIR/scripts/import_files.py --kb KB --source plaud --dir "<导出文件夹>"
python3 SKILL_DIR/scripts/import_files.py --kb KB --source files --label "Apple备忘录" --dir "<文件夹>"
python3 SKILL_DIR/scripts/import_files.py --kb KB --source dingtalk --dir "<文件夹>" --audio   # 文件夹里全是录音转写
```

- 支持 .md .txt .docx .pdf .srt .vtt .html，.zip 自动解压；GBK 编码的 txt 也能读。
- 同一来源每次都重新扫描整个文件夹，所以**同一来源的文件一直放在同一个文件夹**，新导出的往里加就行。
- 内置来源 key：getnote ima feishu feishu_minutes plaud dingtalk iflytek tingwu notion yinxiang youdao files；其他来源自己起一个小写英文 key 并加 `--label`。

## 小白常见问题

- **只导了摘要，没导原文/转写**：录音类平台的总结和逐字稿常常分两个按钮导出，提醒用户两个都要。
- **发来的是 zip 没解压**：不用管，脚本会解压。
- **文件还在手机上**：请用户用微信文件传输助手、AirDrop 或网盘传到电脑，告诉你放在哪个文件夹。
- **PDF 读出来是乱码或空的**：扫描版 PDF 没有文字层，请用户改导 Word/TXT；或装 `pdftotext`（macOS `brew install poppler`）后重跑。
- **提示「有 N 条找不到日期」**：这些笔记用了文件修改时间，通常是导出那天。文件名里带上日期（如 `2026-10-12 客户访谈.txt`）就能修正。
- **改了文件名后重复出现**：note_id 由文件路径算出来，改名会被当成新笔记。已经进了 Obsidian 的旧笔记需要手动删掉。
