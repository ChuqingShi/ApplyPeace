"""Structured LLM calls through the Claude Code CLI (`claude -p`), using the user's own plan.

Every call is tool-less and schema-bound, so the model can only return data;
it cannot browse, run commands or touch files.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from .config import Paths, load_settings


class LLMUnavailable(RuntimeError):
    """The LLM is not consented to, not installed, or failed."""


def available(p: Paths) -> bool:
    return bool(load_settings(p)["llm"].get("consent")) and shutil.which("claude") is not None


def ask_json(p: Paths, system: str, prompt: str, schema: dict, model: str | None = None, timeout: int = 180) -> dict:
    if not load_settings(p)["llm"].get("consent"):
        raise LLMUnavailable("LLM use is off (setup consent not given)")
    if shutil.which("claude") is None:
        raise LLMUnavailable("Claude Code CLI not found")
    model = model or load_settings(p)["llm"].get("model", "sonnet")
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--system-prompt", system,
        "--tools", "",
        "--json-schema", json.dumps(schema),
    ]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=p.home)
    except subprocess.TimeoutExpired as exc:
        raise LLMUnavailable("LLM call timed out") from exc
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"unreadable LLM output: {proc.stderr.strip()[:200]}") from exc
    if out.get("is_error") or out.get("structured_output") is None:
        raise LLMUnavailable(f"LLM error: {out.get('terminal_reason') or out.get('result')}")
    return out["structured_output"]
