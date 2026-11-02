"""Settings you change on the dashboard's Settings page, kept in data/settings.json.

API keys entered there go to data/secrets.json (owner-only permissions) and are never shown again.
Keys in the environment (.env) win over saved ones.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def find_cli(name: str) -> str | None:
    """systemd's PATH skips ~/.local/bin and npm's global bin, where the agent CLIs usually live."""
    for p in (shutil.which(name), f"~/.local/bin/{name}", f"~/.npm-global/bin/{name}", f"/usr/local/bin/{name}"):
        if p and Path(os.path.expanduser(p)).is_file():
            return os.path.expanduser(p)
    return None

PROVIDERS = {
    "claude": {"name": "Claude subscription", "via": "Claude Code CLI", "models": ["haiku", "sonnet", "opus"], "default": "haiku"},
    "codex": {"name": "OpenAI subscription", "via": "Codex CLI (ChatGPT login)", "models": [], "default": ""},
    "jev": {"name": "Jev", "via": "TypeSafe decision model on OpenRouter", "models": ["typesafe/jev-1.14", "~typesafe/jev-latest"],
            "default": "typesafe/jev-1.14", "key": "OPENROUTER_API_KEY"},
    "openrouter": {"name": "OpenRouter", "via": "any chat model, pay per token", "models": [], "default": "", "key": "OPENROUTER_API_KEY"},
    "ollama": {"name": "Ollama", "via": "a local model on this server", "models": [], "default": "", "base_url": "http://localhost:11434"},
}
DEFAULT_ORDER = ["claude", "codex", "jev", "openrouter", "ollama"]
DAILY_MODELS = ["", "opus", "sonnet", "haiku"]  # "" = Claude Code's default
DAILY_AGENTS = {"claude": "Claude Code (Claude subscription)", "codex": "Codex CLI (ChatGPT subscription)"}


def _legacy(raw: dict) -> dict:
    """What config.yaml's email_classifier block meant, for servers set up before the Settings page."""
    c = raw.get("email_classifier") or {}
    b = c.get("backend")
    if b == "none":
        return {"enabled": []}
    if b == "openai":
        name = "ollama" if "11434" in (c.get("base_url") or "") else "openrouter"
        out = {"enabled": [name], "order": [name] + [p for p in DEFAULT_ORDER if p != name], "models": {name: c.get("model", "")}}
        if name == "ollama":
            out["ollama_url"] = (c.get("base_url") or "").removesuffix("/v1")
        return out
    if b == "claude" and c.get("model"):
        return {"models": {"claude": c["model"]}}
    return {}


class Prefs:
    def __init__(self, data_dir: Path, raw: dict | None = None):
        self.path = data_dir / "settings.json"
        self.secrets_path = data_dir / "secrets.json"
        saved = self._read(self.path)
        # Out of the box, use whichever subscription CLI is installed.
        agent = "codex" if find_cli("codex") and not find_cli("claude") else "claude"
        base = {"order": list(DEFAULT_ORDER), "enabled": [agent], "models": {}, "min_confidence": 0.7,
                "daily_agent": agent, "daily_model": "", "daily_codex_model": "",
                "ollama_url": PROVIDERS["ollama"]["base_url"]}
        base.update(_legacy(raw or {}) if not saved else {})
        base.update(saved)
        base["order"] = [p for p in base["order"] if p in PROVIDERS] + [p for p in DEFAULT_ORDER if p not in base["order"]]
        self.data = base

    @staticmethod
    def _read(p: Path) -> dict:
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return {}

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=1))

    def model(self, provider: str) -> str:
        return self.data["models"].get(provider) or PROVIDERS[provider]["default"]

    def chain(self) -> list[str]:
        """Enabled sources in the order they're tried."""
        return [p for p in self.data["order"] if p in self.data["enabled"]]

    def key(self, name: str) -> str | None:
        return os.environ.get(name) or self._read(self.secrets_path).get(name) or None

    def set_key(self, name: str, value: str):
        s = self._read(self.secrets_path)
        if value:
            s[name] = value
        else:
            s.pop(name, None)
        self.secrets_path.write_text(json.dumps(s))
        os.chmod(self.secrets_path, 0o600)

    def key_hint(self, name: str) -> str:
        k = self.key(name)
        src = "in .env" if os.environ.get(name) else "saved here"
        return f"set ({src}, ends {k[-4:]})" if k else "not set"
