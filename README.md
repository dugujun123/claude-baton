# claude-baton

**主模型用不了的时候，把整个 Claude Code 会话交给后台另一个 Agent 接着干。**
*When your main model runs out, hand the whole Claude Code session to a background agent CLI.* [English below](#english)

## 它解决什么

额度用完、渠道断了，常见做法是切到别的模型继续。切过去之后，你会发现两个问题：

- 新模型接不住你的那套规则、skill 和习惯，干出来的东西不对味。
- 有的渠道在 Claude Code 里根本接不进去，可它自家的命令行是能用的。

baton 的做法：你留在原来的 Claude Code 会话里，活交给后台的 Agent 命令行去干。你发的每条消息由钩子**原样**转过去，它的回复原样显示回来。前台模型只负责展示，碰不到你的原话。

## 怎么来的

我自己的主渠道额度用完以后，另一个渠道在客户端里被平台拦了，发什么都报错。查下来，这个平台不支持外部客户端直接接，但它官方的命令行可以在后台跑。于是改成：客户端里留一个便宜的模型当前台，活交给官方命令行。

第一版让前台模型自己转发，跑了一篇文章的完整流程，问题全出在转发这一层：它会改写我的原话、往消息里加自己的意见、还自己开了子会话，越转越慢。后来改成由钩子程序转发，前台模型不经手。再测一轮，13 轮回复贴出来的内容和后台原文逐字一致。

## 安装

需要 Python 3.9 以上，以及至少一个后台 Agent 命令行（目前内置 droid 适配器）。

```bash
git clone https://github.com/dugujun123/claude-baton ~/claude-baton
mkdir -p ~/.config/baton
cp ~/claude-baton/config.example.json ~/.config/baton/config.json
cp ~/claude-baton/templates/agent_prompt.md ~/.config/baton/agent_prompt.md   # 写上你自己的规则路径
```

加载插件（任选一种）：

- 命令行：`claude --plugin-dir ~/claude-baton`
- 桌面客户端：在 `~/.claude/settings.json` 的 `env` 里加 `"CLAUDE_CODE_PLUGIN_DIRS": "/Users/你/claude-baton"`，重启客户端。

droid 适配器需要 `droid` 命令和它的 API Key：设好环境变量 `FACTORY_API_KEY`，或在配置里用 `api_key_cmd` 写一条取 Key 的命令（比如从钥匙串取）。

## 用法

1. 照常把请求发给 Claude Code。
2. 输入 `/baton`。前台模型确认这件活后台做得了，就开始接力（第一轮会读你的规则文件，可能要几分钟）。
3. 之后正常聊天。每条消息原样转给后台，回复末尾有一行页脚：模型、耗时、权限档、改了哪些文件。
4. 想跟前台模型说话，消息以 `//` 开头；想结束，发「结束接力」。

权限：默认 `low`，后台只能用编辑工具改文件。要装依赖、构建、跑会写文件的脚本时，开场或中途切到 `medium`（`baton.py auto medium`），页脚会列出它跑过的命令。

## 配置（`~/.config/baton/config.json`）

| 字段 | 作用 |
|---|---|
| `adapter` | 用哪个后台 Agent，对应 `scripts/adapters/<名字>.py` |
| `workdir` | 后台的工作目录，留空就用开始接力时的目录 |
| `agent_prompt` | 每轮附给后台 Agent 的说明文件，写你的规则在哪、哪些目录只读 |
| `protected` | 后台改到这些目录里的文件时，页脚给出警告 |
| `display` | `context`：前台模型原样贴回复（默认）；`block`：钩子直接显示，不经前台模型 |
| `end_words` | 结束接力的词 |
| `suggest_cmd` | 可选。一条命令，返回 0 时，前台模型会在动手前先问你要不要接力（比如判断当前用的是不是某个渠道） |
| `droid.model` / `fallback_model` | 主模型；撞上用量上限时这一轮换用的模型 |

## 后台 Agent 做不了的

- 看截图、图片（客户端里的图片传不过去，存成本地文件写路径可以）。
- 只在 Claude Code 里才有的工具：MCP、页面发布、提问弹窗。
- 读 Claude Code 的会话记录。

## 已测与未测

- 作者自用版在 macOS、Claude 桌面客户端、droid 适配器下实测过：一篇文章从选题到成稿的完整写作流程，以及改文件、跑命令的非写作活。开源版是从自用版整理出来的，做过冒烟测试（开场、转发、`//` 旁路、其他会话不受影响、结束、改动清单）。
- `display: block` 模式、Linux、其他适配器未测。欢迎提适配器：在 `scripts/adapters/` 下实现一个 `Adapter.run()`，接口写在 `scripts/adapters/__init__.py`。

## 许可

MIT

---

## English

**claude-baton** keeps you in your Claude Code session when your main model is unavailable, and lets a background agent CLI do the work.

- A `UserPromptSubmit` hook forwards every message **verbatim** to the background agent and hands its reply back. The front model only displays it, so it cannot reword your request or add its own opinions.
- Your instructions file (`agent_prompt`) is attached every turn, so your rules, skills and conventions survive the model change.
- The relay is bound to the session that started it; other sessions are untouched. `//` talks to the front model, `结束接力` / `end relay` stops.
- A footer lists the model, time, permission level and every file the agent edited, with a warning for protected paths.

**Why:** when one channel stopped working inside the client, its official CLI still worked in the background. Letting the front model relay messages failed: it reworded prompts, injected its own notes and spawned extra sessions. Moving the relay into a hook fixed it: in testing, 13 of 13 replies matched the background output exactly.

**Install:** clone, copy `config.example.json` and `templates/agent_prompt.md` into `~/.config/baton/`, then load with `claude --plugin-dir ~/claude-baton` (or `CLAUDE_CODE_PLUGIN_DIRS` for the desktop app). The bundled `droid` adapter needs the `droid` CLI and `FACTORY_API_KEY` (or `api_key_cmd`).

**Use:** send your request, type `/baton`, then keep chatting. Permission level `low` (edit tools only) by default; switch to `medium` with `baton.py auto medium` when the agent needs to run commands that write files.

**Can't relay:** images in the chat, Claude Code–only tools (MCP, page publishing, question dialogs), reading Claude Code transcripts.

**Tested:** the author's own version on macOS, Claude desktop app, droid adapter (a full writing workflow plus file and command tasks); this open version is a cleaned-up extract with a smoke test. Untested: `display: block`, Linux, other adapters. Adapter contract: `scripts/adapters/__init__.py`.

License: MIT
