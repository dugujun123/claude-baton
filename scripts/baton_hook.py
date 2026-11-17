#!/usr/bin/env python3
"""UserPromptSubmit hook.

Relay open in this session: forward the prompt verbatim to the background
agent and hand its reply back, so the front model never rewrites either side.
  - an end word (config "end_words") closes the relay
  - a prompt starting with // stays with the front model
  - messages the client injects itself (task notifications, cross-session
    notes, interrupts) are ignored
No relay: remember the prompt so `baton.py start --from-history` uses the
user's own words; if "suggest_cmd" exits 0, remind the front model to offer
the relay before doing any work.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import baton  # noqa: E402

INJECTED = ("<task-notification>", "<system-reminder>", "<cross-session-message",
            "Another Claude session sent a message", "[Request interrupted")
SUGGEST = (
    "【baton】当前渠道符合接力条件。如果这条消息要你动手干活，并且本会话里用户还没选过，"
    "先用编号选项问用户：1. 交给后台 Agent 接力（/baton，之后的消息由钩子原样转发）；"
    "2. 你自己做；3. 先不做。用户选了再继续。纯聊天直接回答，忽略本提醒。"
)


def context(text):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                             "additionalContext": text}}, ensure_ascii=False))


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    prompt = data.get("prompt") or ""
    session = data.get("session_id")
    if prompt.lstrip().startswith(INJECTED):
        return
    cfg = baton.config()
    state = baton.active_state()

    if not state or state.get("owner") != session:
        baton.remember_prompt(prompt, session)
        cmd = cfg.get("suggest_cmd")
        if cmd and not state and subprocess.run(cmd, shell=True, capture_output=True).returncode == 0:
            context(SUGGEST)
        return

    text = prompt.strip()
    if text.startswith("//"):
        context("【baton】这条以 // 开头，是用户对你说的，不转发。正常回答即可，接力仍在进行。")
        return
    if text in cfg["end_words"] or text in ("/baton end",):
        context(f"【baton】{baton.do_end()} 用一句话告诉用户，之后的消息不再转发。")
        return

    try:
        res, st = baton.do_say(prompt, cfg)
        out = baton.render(res, st, baton.expand(st["workdir"]))
    except SystemExit:
        out = "后台 Agent 这一轮没能执行，详见 ~/.config/baton/runs.jsonl。"
    except Exception as e:  # noqa: BLE001
        out = f"转发出错：{type(e).__name__}: {e}"

    if cfg.get("display") == "block":
        print(json.dumps({"decision": "block", "reason": out}, ensure_ascii=False))
        return
    context("【baton 接力回复】钩子已把用户这条消息原样转给后台 Agent，下面是它的回复。"
            "你这一轮只做一件事：把 BEGIN 和 END 两行之间的内容原样贴给用户，"
            "不改写、不删减、不总结、不加评论、不调用任何工具。\n"
            f"BEGIN\n{out}\nEND")


if __name__ == "__main__":
    main()
