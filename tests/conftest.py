import textwrap

import pytest

import config

MINIMAL_CONFIG = textwrap.dedent("""
    nowModel: "openai.deepseek"
    providers:
      openai:
        deepseek:
          enabled: true
          base_url: "https://api.deepseek.com"
          default_model: "deepseek-v4-pro"
          env_key: "DEEPSEEK_API_KEY"
        disabled_one:
          enabled: false
          base_url: "https://example.com/v1"
          default_model: "whatever"
    topics:
      dp: "动态规划"
    """)


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Point CP_AGENT_CONFIG at a minimal valid config; yields the path for edits."""
    path = tmp_path / "config.yaml"
    path.write_text(MINIMAL_CONFIG, encoding="utf-8")
    monkeypatch.setenv("CP_AGENT_CONFIG", str(path))
    config.load_config.cache_clear()
    yield path
    config.load_config.cache_clear()


@pytest.fixture
def tmp_problem_dir(tmp_path):
    """Bare problem directory with the standard layout."""
    problem_dir = tmp_path / "problem"
    for sub in ("bin", "inputs", "outputs"):
        (problem_dir / sub).mkdir(parents=True)
    return problem_dir
