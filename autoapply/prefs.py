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
PAID = {"jev", "openrouter"}  # billed per token; the others run on a subscription or locally
DAILY_MODELS = ["", "opus", "sonnet", "haiku"]  # "" = Claude Code's default
DAILY_AGENTS = {"claude": "Claude Code (Claude subscription)", "codex": "Codex CLI (ChatGPT subscription)"}
# The daily run's model can come from the agent's subscription or from an OpenRouter key (paid per token).
DAILY_VIA = {"subscription": "Your subscription", "openrouter": "OpenRouter key"}
DAILY_OR_DEFAULT = {"claude": "~anthropic/claude-sonnet-latest", "codex": ""}


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
                "daily_via": "subscription", "daily_or_model": "",
                # The run is split into short stages. Finding, ranking, prep packs and drafts use the light
                # model; each application (tailoring + form) uses the main one above.
                "daily_light_model": "sonnet", "daily_codex_light_model": "", "daily_or_light_model": "",
                "quick_pick_model": "",  # an OpenRouter model: picks the day's roles in one call instead of an agent session
                "discover_weekday": 1,  # 1 = Monday ... 7 = Sunday; 0 = never look for new companies
                "ollama_url": PROVIDERS["ollama"]["base_url"],
                # Cost controls: what the daily run may do per day, and a cap on money spent on paid APIs.
                "picks_per_day": 2, "prep_enabled": True, "prep_per_day": 2, "drafts_enabled": True,
                "drafts_per_day": 5, "monthly_api_budget": 5.0}
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

    def chain(self, spent: float = 0.0) -> list[str]:
        """Enabled sources in the order they're tried. Paid sources drop out once this month's spend
        reaches the budget."""
        over = spent >= float(self.data.get("monthly_api_budget") or 0)
        return [p for p in self.data["order"] if p in self.data["enabled"] and not (over and p in PAID)]

    LIGHT_STAGES = ("rank", "prep", "drafts", "alerts")

    def daily(self, spent: float = 0.0, stage: str = "apply") -> dict:
        """What the daily run uses: agent, model, and where the model comes from. via is 'blocked' when it
        would use OpenRouter but this month's spend has reached the budget, or no key is set."""
        d = self.data
        agent = d.get("daily_agent") if d.get("daily_agent") in DAILY_AGENTS else "claude"
        via = d.get("daily_via") if d.get("daily_via") in DAILY_VIA else "subscription"
        light = stage in self.LIGHT_STAGES
        if via == "openrouter":
            model = d.get("daily_or_model") or DAILY_OR_DEFAULT[agent]
            if light and d.get("daily_or_light_model"):
                model = d["daily_or_light_model"]
            why = ("no OpenRouter key" if not self.key("OPENROUTER_API_KEY") else
                   "no OpenRouter model picked" if not model else
                   "monthly API budget reached" if spent >= float(d.get("monthly_api_budget") or 0) else "")
            return {"agent": agent, "model": model, "via": "blocked" if why else via, "why": why}
        model = d.get("daily_codex_model" if agent == "codex" else "daily_model") or ""
        if light:
            model = d.get("daily_codex_light_model" if agent == "codex" else "daily_light_model") or model
        return {"agent": agent, "model": model, "via": via, "why": ""}

    def limits(self) -> dict:
        d = self.data
        picks = int(d["picks_per_day"])
        if os.environ.get("AUTOAPPLY_PICKS", "").isdigit():  # "Run now" on the dashboard: this many roles, this run
            picks = max(1, min(5, int(os.environ["AUTOAPPLY_PICKS"])))
        return {"picks_per_day": picks,
                "prep_per_day": int(d["prep_per_day"]) if d["prep_enabled"] else 0,
                "drafts_per_day": int(d["drafts_per_day"]) if d["drafts_enabled"] else 0}

    def key(self, name: str) -> str | None:
        return os.environ.get(name) or self._read(self.secrets_path).get(name) or None

    def from_env(self, name: str) -> bool:
        from .config import FROM_SECRETS
        return bool(os.environ.get(name)) and name not in FROM_SECRETS

    def set_key(self, name: str, value: str):
        """Saves to data/secrets.json and applies it to this process, unless .env already sets it."""
        from .config import FROM_SECRETS
        s = self._read(self.secrets_path)
        if value:
            s[name] = value
        else:
            s.pop(name, None)
        self.secrets_path.touch(mode=0o600, exist_ok=True)
        os.chmod(self.secrets_path, 0o600)
        self.secrets_path.write_text(json.dumps(s))
        if not self.from_env(name):
            if value:
                os.environ[name] = value
                FROM_SECRETS.add(name)
            else:
                os.environ.pop(name, None)
                FROM_SECRETS.discard(name)

    def key_hint(self, name: str) -> str:
        k = self.key(name)
        src = "in .env" if self.from_env(name) else "saved here"
        return f"set ({src}, ends {k[-4:]})" if k else "not set"
