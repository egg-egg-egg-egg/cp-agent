import pytest

import config
from config import ConfigError, get_provider, resolve_api_key


def test_missing_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CP_AGENT_CONFIG", str(tmp_path / "nope.yaml"))
    config.load_config.cache_clear()
    with pytest.raises(ConfigError, match="config.yaml.example"):
        config.load_config()
    config.load_config.cache_clear()


def test_invalid_yaml(tmp_config):
    tmp_config.write_text("providers: [unclosed", encoding="utf-8")
    config.load_config.cache_clear()
    with pytest.raises(ConfigError, match="YAML"):
        config.load_config()


def test_validation_collects_all_problems(tmp_config):
    tmp_config.write_text(
        "nowModel: 'openai.ghost'\nproviders:\n  openai:\n    x:\n      base_url: ''\n",
        encoding="utf-8",
    )
    config.load_config.cache_clear()
    with pytest.raises(ConfigError) as ei:
        config.load_config()
    msg = str(ei.value)
    assert "base_url" in msg
    assert "nowModel" in msg
    assert "topics" in msg


def test_get_provider_aliases(tmp_config):
    assert get_provider("deepseek") == get_provider("openai.deepseek")
    protocol, cfg = get_provider(None)  # falls back to nowModel
    assert protocol == "openai"
    assert cfg["default_model"] == "deepseek-v4-pro"


def test_get_provider_disabled(tmp_config):
    with pytest.raises(ValueError, match="disabled"):
        get_provider("disabled_one")


def test_get_provider_unknown(tmp_config):
    with pytest.raises(KeyError):
        get_provider("ghost")


def test_lazy_attributes(tmp_config):
    assert config.DEFAULT_STRESS_ITERATIONS == 10000
    assert config.ALGO_TOPICS == {"dp": "动态规划"}
    with pytest.raises(AttributeError):
        config.NOT_A_REAL_ATTR


# ─── resolve_api_key ─────────────────────────────────────────────────────────

def test_resolve_cli_key_wins():
    assert resolve_api_key({"api_key": "sk-cfg"}, cli_key="sk-cli") == "sk-cli"


def test_resolve_api_key_field():
    assert resolve_api_key({"api_key": "sk-cfg", "env_key": "NOPE"}) == "sk-cfg"


def test_resolve_env_key_hit(monkeypatch):
    monkeypatch.setenv("MY_TEST_KEY", "sk-env")
    assert resolve_api_key({"env_key": "MY_TEST_KEY"}) == "sk-env"


def test_resolve_env_key_unset_names_variable(monkeypatch):
    monkeypatch.delenv("MY_TEST_KEY", raising=False)
    with pytest.raises(ConfigError, match="MY_TEST_KEY"):
        resolve_api_key({"env_key": "MY_TEST_KEY"})


def test_resolve_env_key_literal_key_rejected():
    with pytest.raises(ConfigError, match="明文"):
        resolve_api_key({"env_key": "sk-0000000000000000000000000000dead"})


def test_resolve_nothing_configured():
    with pytest.raises(ConfigError, match="api_key"):
        resolve_api_key({})
