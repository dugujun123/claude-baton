"""droid adapter: runs Factory's official `droid exec` CLI in the background.

Config (config.json -> "droid"):
  model           model id passed to `droid exec -m`
  fallback_model  optional; used once when a turn fails on a usage limit
  auto            default permission level: low (edit tools only) | medium
  api_key_cmd     optional shell command printing the API key; otherwise
                  FACTORY_API_KEY must already be in the environment
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

LIMIT_WORDS = ("rate limit", "usage limit", "limit reached", "quota", "exhausted", "429")
EDIT_TOOLS = {"Create", "Edit", "MultiEdit", "Write"}


class Adapter:
    def __init__(self, cfg):
        self.cfg = cfg
        self.bin = shutil.which("droid") or "droid"

    def _env(self):
        env = dict(os.environ)
        cmd = self.cfg.get("api_key_cmd")
        if cmd:
            key = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()
            if key:
                env["FACTORY_API_KEY"] = key
        return env

    @staticmethod
    def _log(session_id):
        if not session_id:
            return None
        hits = list((Path.home() / ".factory/sessions").glob(f"*/{session_id}.jsonl"))
        return hits[0] if hits else None

    def _count(self, session_id):
        p = self._log(session_id)
        if not p:
            return 0
        with p.open(encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)

    def _tool_uses(self, session_id, skip):
        """Tool calls from droid's own session log. Edits made inside script-style
        tool calls only show up here, not in the stream output."""
        p = self._log(session_id)
        if not p:
            return []
        out = []
        with p.open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i < skip:
                    continue
                try:
                    m = json.loads(line).get("message") or {}
                except json.JSONDecodeError:
                    continue
                for b in (m.get("content") or []) if isinstance(m, dict) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        out.append((b.get("name"), b.get("input") or {}))
        return out

    def _once(self, text, session_id, workdir, auto, prompt_file, timeout, model):
        cmd = [self.bin, "exec", "-m", model, "-o", "stream-json", "--cwd", str(workdir), "--auto", auto]
        if prompt_file and Path(prompt_file).exists():
            cmd += ["--append-system-prompt-file", str(prompt_file)]
        if session_id:
            cmd += ["-s", session_id]
        cmd.append(text)
        skip = self._count(session_id)
        t0 = time.time()
        try:
            r = subprocess.run(cmd, env=self._env(), capture_output=True, text=True, timeout=timeout)
            out, err = r.stdout, r.stderr
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            err = f"timeout {timeout}s"
        reply, error, sid, usage = None, None, session_id, {}
        for line in out.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = ev.get("session_id") or sid
            if ev.get("type") == "completion":
                reply, usage = ev.get("finalText") or "", ev.get("usage") or {}
            elif ev.get("type") == "error":
                error = ev.get("message")
        if reply is None and error is None:
            error = (err or out)[-500:] or "droid 没有输出"
        edits, commands = [], []
        for name, inp in self._tool_uses(sid, skip if session_id else 0):
            if name in EDIT_TOOLS and inp.get("file_path"):
                edits.append(inp["file_path"])
            elif name == "Execute" and inp.get("command"):
                commands.append(inp["command"].strip().replace("\n", " ")[:160])
        return {"ok": reply is not None and error is None, "reply": reply, "error": error,
                "session_id": sid, "usage": usage, "seconds": time.time() - t0,
                "edits": sorted(set(edits)), "commands": commands, "model": model}

    def run(self, text, session_id, workdir, auto, prompt_file, timeout):
        model = self.cfg.get("model") or "claude-opus-5-5"
        res = self._once(text, session_id, workdir, auto, prompt_file, timeout, model)
        fb = self.cfg.get("fallback_model")
        if not res["ok"] and fb and fb != model and any(w in (res["error"] or "").lower() for w in LIMIT_WORDS):
            res = self._once(text, session_id, workdir, auto, prompt_file, timeout, fb)
            res["note"] = f"{model} 撞上用量上限，这一轮换成 {fb}"
        return res
