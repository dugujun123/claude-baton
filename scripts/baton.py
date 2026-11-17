#!/usr/bin/env python3
"""baton: hand a whole Claude Code session to a background agent CLI.

When your main model channel runs out (quota, outage, a client that stops
working), keep the session you are in and let another agent CLI do the work.
A UserPromptSubmit hook forwards every message verbatim; the front model only
shows the reply. Your instructions file travels with it, so rules and context
are not lost when the model changes.

  baton.py start --from-history [--auto low|medium]   start with the user's last request
  baton.py start -f request.md                        start from a file (hook won't auto-forward)
  baton.py say "text"                                 send one message by hand
  baton.py auto low|medium                            change the permission level
  baton.py status | end                               show / close the relay
"""
import argparse
import datetime as dt
import importlib
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home()
CONF_DIR = Path(os.environ.get("BATON_HOME", HOME / ".config/baton"))
CONFIG = CONF_DIR / "config.json"
STATE = CONF_DIR / "state.json"
HISTORY = CONF_DIR / "history.json"
RUNS = CONF_DIR / "runs.jsonl"

DEFAULTS = {
    "adapter": "droid",
    "workdir": "",                       # empty: the directory the relay was started from
    "agent_prompt": str(ROOT / "templates/agent_prompt.md"),
    "protected": ["~/.claude"],          # edits here are flagged in the footer
    "display": "context",                # context: front model pastes the reply; block: shown directly
    "end_words": ["结束接力", "end relay"],
    "suggest_cmd": "",                   # optional: exit 0 means "offer the relay before work"
    "timeout": 840,
    "droid": {"model": "claude-opus-5-5", "fallback_model": "", "auto": "low"},
}
OPTION_PICK = re.compile(r"^\s*(选)?\s*(第)?\s*[1-3一二三]\s*(个|项|号)?\s*[。.!！]?\s*$")


def die(msg, code=2):
    print(f"baton: {msg}", file=sys.stderr)
    sys.exit(code)


def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def config():
    c = json.loads(json.dumps(DEFAULTS))
    user = load_json(CONFIG, {}) or {}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k].update(v)
        else:
            c[k] = v
    return c


def expand(p):
    return Path(os.path.expanduser(p)).resolve()


def inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def adapter(cfg):
    name = cfg["adapter"]
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        mod = importlib.import_module(f"adapters.{name}")
    except ImportError:
        die(f"没有名为 {name} 的适配器（scripts/adapters/{name}.py）")
    return mod.Adapter(cfg.get(name, {}))


def active_state():
    s = load_json(STATE)
    return s if s and not s.get("ended") else None


def remember_prompt(prompt, session=None):
    text = prompt.strip()
    if text.startswith("/baton"):
        text = text[len("/baton"):].strip()
        if text.split(" ", 1)[0] in ("start", "end", "status", "auto", "say"):
            return
    if not text:
        return
    h = load_json(HISTORY, []) or []
    h.append({"time": dt.datetime.now().isoformat(timespec="seconds"), "session": session, "prompt": text})
    save_json(HISTORY, h[-10:])


def latest_request():
    for item in reversed(load_json(HISTORY, []) or []):
        p = (item.get("prompt") or "").strip()
        if p and not OPTION_PICK.match(p) and not p.startswith("/"):
            return p, item.get("session")
    return None, None


def log_run(entry):
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with RUNS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def turn(text, state, cfg):
    ad = adapter(cfg)
    workdir = expand(state["workdir"])
    result = ad.run(text, state.get("session_id"), workdir, state.get("auto", "low"),
                    expand(cfg["agent_prompt"]), cfg["timeout"])
    protected = [expand(p) for p in cfg["protected"]]
    bad = [p for p in result["edits"] if any(inside(Path(p), q) or Path(p) == q for q in protected)]
    entry = {
        "time": dt.datetime.now().isoformat(timespec="seconds"),
        "adapter": cfg["adapter"], "model": result.get("model"), "auto": state.get("auto", "low"),
        "ok": result["ok"], "session_id": result.get("session_id"), "seconds": result.get("seconds"),
        "usage": result.get("usage"), "edits": len(result["edits"]), "protected_edits": len(bad),
        "message": text.strip().replace("\n", " ")[:120],
    }
    if result.get("error"):
        entry["error"] = result["error"][-300:]
    log_run(entry)
    result["bad"] = bad
    return result


def render(res, state, workdir):
    lines = []
    if res.get("reply") is not None:
        lines.append(res["reply"])
    if res.get("error"):
        lines.append(f"后台 Agent 这一轮出错：{res['error']}")
    lines += ["", "---",
              f"baton: {'成功' if res['ok'] else '失败'} · {res.get('model')} · {res.get('seconds') or 0:.0f}s"
              f" · 权限 {state.get('auto', 'low')} · 改动文件 {len(res['edits'])} 个"]
    if res.get("note"):
        lines.append(f"换模型: {res['note']}")
    for p in res["edits"][:50]:
        lines.append(f"  改动: {str(p).replace(str(workdir) + '/', '')}")
    if state.get("auto") == "medium":
        for c in res.get("commands", [])[:20]:
            lines.append(f"  命令: {c}")
    if res.get("bad"):
        lines.append("警告：后台 Agent 改了受保护目录里的文件，请核对：")
        lines += [f"  {p}" for p in res["bad"]]
    return "\n".join(lines)


def do_start(text, cfg, owner=None, auto=None, force=False):
    if active_state() and not force:
        die(f"已有进行中的接力 {active_state().get('session_id')}。继续发消息即可；确定要新开加 --force。")
    state = {"session_id": None, "owner": owner, "auto": auto or cfg.get(cfg["adapter"], {}).get("auto", "low"),
             "workdir": str(expand(cfg["workdir"]) if cfg["workdir"] else Path.cwd())}
    res = turn(text, state, cfg)
    if res["ok"] and res.get("session_id"):
        state.update(session_id=res["session_id"], started=dt.datetime.now().isoformat(timespec="seconds"), turns=1)
        save_json(STATE, state)
    return res, state


def do_say(text, cfg):
    state = active_state()
    if not state:
        die("没有进行中的接力，先 start。")
    res = turn(text, state, cfg)
    state["turns"] = state.get("turns", 0) + 1
    state["last"] = dt.datetime.now().isoformat(timespec="seconds")
    save_json(STATE, state)
    return res, state


def do_end():
    state = active_state()
    if not state:
        return "没有进行中的接力。"
    state["ended"] = dt.datetime.now().isoformat(timespec="seconds")
    save_json(STATE, state)
    return f"接力已结束，共 {state.get('turns')} 轮。"


def main():
    ap = argparse.ArgumentParser(description="baton：把整个会话交给后台 Agent 接力")
    ap.add_argument("cmd", choices=["start", "say", "status", "end", "auto"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("-f", "--file")
    ap.add_argument("--from-history", action="store_true")
    ap.add_argument("--auto", choices=["low", "medium"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    cfg = config()

    if a.cmd == "status":
        s = load_json(STATE)
        print(json.dumps(s, ensure_ascii=False, indent=2) if s else "没有接力记录。")
        return
    if a.cmd == "end":
        print(do_end())
        return
    if a.cmd == "auto":
        state = active_state()
        if not state or a.text not in ("low", "medium"):
            die("用法：baton.py auto low|medium（需要进行中的接力）")
        state["auto"] = a.text
        save_json(STATE, state)
        print(f"权限已改为 {a.text}，下一轮起生效。")
        return

    owner = None
    if a.cmd == "start" and a.from_history:
        text, owner = latest_request()
        if not text:
            die("钩子没记到用户的请求。请用户把请求再发一遍。")
    elif a.file:
        text = Path(a.file).expanduser().read_text(encoding="utf-8")
    else:
        text = a.text or ""
    if not text.strip():
        die("没有消息内容")

    res, state = do_start(text, cfg, owner, a.auto, a.force) if a.cmd == "start" else do_say(text, cfg)
    print(render(res, state, expand(state["workdir"])))
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
