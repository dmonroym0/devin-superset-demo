"""Environment-backed application settings."""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass

from app.models import FORK_REPO, Mode


class ConfigError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str = ""):
        self._value = value

    def get(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    def __str__(self) -> str:
        return "Secret(***)"

    def __bool__(self) -> bool:
        return bool(self._value)


@dataclass(frozen=True)
class Settings:
    mode: Mode
    github_repo: str
    github_api_base: str
    github_token: Secret
    github_webhook_secret: Secret
    devin_api_base: str
    devin_api_key: Secret
    devin_org_id: str | None
    playbook_triage_id: str | None
    playbook_fix_id: str | None
    playbook_triage_title: str
    playbook_fix_title: str
    triage_acu_cap: int
    fix_acu_cap: int
    acu_ceiling: int
    sweep_interval_s: int
    poll_interval_s: int
    soft_timeout_s: int
    hard_timeout_s: int
    devin_mode_triage: str | None
    devin_mode_fix: str | None
    trigger_label: str
    db_path: str
    host: str
    port: int
    # Host curls through Compose's 127.0.0.1:8000:8000 publish arrive from its 172.16/12 bridge gateway.
    sweep_allowed_cidrs: tuple[str, ...] = ("127.0.0.0/8", "::1/128", "172.16.0.0/12")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        raw_mode = env.get("APP_MODE", "demo")
        problems: list[str] = []
        try:
            mode = Mode(raw_mode)
        except ValueError:
            problems.append("APP_MODE invalid")
            mode = Mode.DEMO

        def integer(name: str, default: int) -> int:
            raw = env.get(name, str(default))
            try:
                result = int(raw)
            except (TypeError, ValueError):
                problems.append(f"{name} invalid")
                return default
            if result <= 0:
                problems.append(f"{name} invalid")
                return default
            return result

        triage_cap = integer("TRIAGE_ACU_CAP", 5)
        fix_cap = integer("FIX_ACU_CAP", 15)
        ceiling = integer("ACU_CEILING", 120)
        sweep = integer("SWEEP_INTERVAL_S", 300)
        poll_default = 1 if mode is Mode.DEMO else 15
        poll = integer("POLL_INTERVAL_S", poll_default)
        soft_timeout = integer("SOFT_TIMEOUT_S", 1800)
        hard_timeout = integer("HARD_TIMEOUT_S", 5400)
        port = integer("PORT", 8000)
        sweep_allowed_cidrs = tuple(
            part.strip()
            for part in env.get("SWEEP_ALLOWED_CIDRS", "127.0.0.0/8,::1/128,172.16.0.0/12").split(",")
            if part.strip()
        )
        try:
            if not sweep_allowed_cidrs:
                raise ValueError
            for cidr in sweep_allowed_cidrs:
                ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            problems.append("SWEEP_ALLOWED_CIDRS invalid")
        if triage_cap > ceiling:
            problems.append("TRIAGE_ACU_CAP invalid")
        if fix_cap > ceiling:
            problems.append("FIX_ACU_CAP invalid")
        if problems:
            raise ConfigError(problems)

        webhook_secret = env.get("GITHUB_WEBHOOK_SECRET", "")
        if not webhook_secret and mode is Mode.DEMO:
            webhook_secret = "demo-only-not-a-secret"

        def optional(name: str) -> str | None:
            return env.get(name) or None

        return cls(
            mode=mode,
            github_repo=FORK_REPO,
            github_api_base=env.get("GITHUB_API_BASE", "https://api.github.com"),
            github_token=Secret(env.get("GITHUB_TOKEN", "")),
            github_webhook_secret=Secret(webhook_secret),
            devin_api_base=env.get("DEVIN_API_BASE", "https://api.devin.ai"),
            devin_api_key=Secret(env.get("DEVIN_API_KEY", "")),
            devin_org_id=optional("DEVIN_ORG_ID"),
            playbook_triage_id=optional("PLAYBOOK_TRIAGE_ID"),
            playbook_fix_id=optional("PLAYBOOK_FIX_ID"),
            playbook_triage_title=env.get("PLAYBOOK_TRIAGE_TITLE", "CVE Reachability Triage (Read-Only)"),
            playbook_fix_title=env.get("PLAYBOOK_FIX_TITLE", "Dependency Security Fix (superset)"),
            triage_acu_cap=triage_cap,
            fix_acu_cap=fix_cap,
            acu_ceiling=ceiling,
            sweep_interval_s=sweep,
            poll_interval_s=poll,
            soft_timeout_s=soft_timeout,
            hard_timeout_s=hard_timeout,
            devin_mode_triage=optional("DEVIN_MODE_TRIAGE"),
            devin_mode_fix=optional("DEVIN_MODE_FIX"),
            trigger_label="devin:fixplease",
            db_path=env.get("DB_PATH", "data/forkfix.db"),
            host=env.get("HOST", "0.0.0.0"),
            port=port,
            sweep_allowed_cidrs=sweep_allowed_cidrs,
        )

    def redacted(self) -> dict[str, str]:
        return {
            "GITHUB_TOKEN": "set" if self.github_token else "missing",
            "GITHUB_WEBHOOK_SECRET": "set" if self.github_webhook_secret else "missing",
            "DEVIN_API_KEY": "set" if self.devin_api_key else "missing",
            "DEVIN_ORG_ID": "set" if self.devin_org_id else "missing",
            "PLAYBOOK_TRIAGE_ID": "set" if self.playbook_triage_id else "missing",
            "PLAYBOOK_FIX_ID": "set" if self.playbook_fix_id else "missing",
        }
