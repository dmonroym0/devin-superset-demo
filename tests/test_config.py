import pytest

from app.config import ConfigError, Secret, Settings
from app.models import FORK_REPO, Mode


def test_demo_defaults():
    settings = Settings.from_env({})

    assert settings.mode is Mode.DEMO
    assert settings.github_repo == FORK_REPO
    assert settings.github_api_base == "https://api.github.com"
    assert settings.devin_api_base == "https://api.devin.ai"
    assert settings.github_webhook_secret.get() == "demo-only-not-a-secret"
    assert settings.triage_acu_cap == 5
    assert settings.fix_acu_cap == 15
    assert settings.acu_ceiling == 120
    assert settings.poll_interval_s == 1
    assert settings.port == 8000
    assert settings.devin_mode_triage is None
    assert settings.devin_mode_fix is None


def test_live_defaults_do_not_require_credentials():
    settings = Settings.from_env({"APP_MODE": "live"})

    assert settings.mode is Mode.LIVE
    assert settings.github_webhook_secret.get() == ""
    assert settings.poll_interval_s == 15


def test_upstream_sync_defaults_by_mode():
    demo = Settings.from_env({})
    live = Settings.from_env({"APP_MODE": "live"})

    assert demo.upstream_sync_enabled is True
    assert live.upstream_sync_enabled is False
    assert demo.upstream_sync_interval_s == 86400
    assert demo.upstream_sync_branch == "master"
    assert demo.demo_upstream_scenario == "merge"


def test_upstream_sync_settings_can_be_overridden():
    settings = Settings.from_env(
        {
            "APP_MODE": "demo",
            "UPSTREAM_SYNC_ENABLED": "false",
            "UPSTREAM_SYNC_INTERVAL_S": "3600",
            "UPSTREAM_SYNC_BRANCH": "stable",
            "DEMO_UPSTREAM_SCENARIO": "conflict",
        }
    )

    assert settings.upstream_sync_enabled is False
    assert settings.upstream_sync_interval_s == 3600
    assert settings.upstream_sync_branch == "stable"
    assert settings.demo_upstream_scenario == "conflict"


@pytest.mark.parametrize(
    "environment",
    [
        {"UPSTREAM_SYNC_ENABLED": "sometimes"},
        {"UPSTREAM_SYNC_INTERVAL_S": "0"},
        {"DEMO_UPSTREAM_SCENARIO": "missing"},
    ],
)
def test_invalid_upstream_sync_settings_are_rejected(environment):
    with pytest.raises(ConfigError):
        Settings.from_env(environment)


def test_secret_repr_and_string_hide_value():
    secret = Secret("very-sensitive-value")

    assert repr(secret) == "Secret(***)"
    assert str(secret) == "Secret(***)"
    assert secret.get() == "very-sensitive-value"
    assert not Secret("")
    assert Secret("present")


def test_redacted_reports_only_secret_and_id_presence():
    settings = Settings.from_env(
        {
            "GITHUB_TOKEN": "token-value",
            "DEVIN_API_KEY": "api-value",
            "DEVIN_ORG_ID": "org-placeholder",
            "PLAYBOOK_FIX_ID": "playbook-placeholder",
        }
    )

    assert settings.redacted() == {
        "GITHUB_TOKEN": "set",
        "GITHUB_WEBHOOK_SECRET": "set",
        "DEVIN_API_KEY": "set",
        "DEVIN_ORG_ID": "set",
        "PLAYBOOK_TRIAGE_ID": "missing",
        "PLAYBOOK_FIX_ID": "set",
    }


@pytest.mark.parametrize(
    ("environment", "bad_value"),
    [
        ({"PORT": "not-an-int"}, "not-an-int"),
        ({"APP_MODE": "invalid-mode"}, "invalid-mode"),
        ({"TRIAGE_ACU_CAP": "121"}, "121"),
    ],
)
def test_config_errors_name_variables_without_echoing_values(environment, bad_value):
    with pytest.raises(ConfigError) as error:
        Settings.from_env(environment)

    assert bad_value not in str(error.value)
    assert "invalid" in str(error.value)
