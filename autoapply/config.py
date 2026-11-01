"""Loads config.yaml and profile.yaml; paths resolve relative to the config file."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Settings:
    root: Path
    raw: dict
    profile: dict

    @property
    def data_dir(self) -> Path:
        p = self.path(self.raw.get("data_dir", "./data"))
        p.mkdir(parents=True, exist_ok=True)
        return p

    def path(self, value: str) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (self.root / p).resolve()


def load(config_path: str = "config.yaml", profile_path: str | None = None) -> Settings:
    cfg = Path(config_path).resolve()
    raw = yaml.safe_load(cfg.read_text())
    prof = Path(profile_path).resolve() if profile_path else cfg.parent / "profile.yaml"
    profile = yaml.safe_load(prof.read_text()) if prof.exists() else {}
    return Settings(root=cfg.parent, raw=raw, profile=profile)
