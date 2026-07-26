"""
CP-Agent configuration loader.

Reads config.yaml lazily: `import config` never touches the filesystem;
the file is loaded and validated on first access of a config-derived
attribute (PEP 562 module __getattr__). Missing/invalid config raises
ConfigError with a friendly message instead of a raw traceback.
"""
import functools
import os
import re
from pathlib import Path

import yaml

# ─── Paths (eager, no yaml needed) ───────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
PROBLEMS_DIR = PROJECT_ROOT / "problems"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
BIN_DIR = PROJECT_ROOT / "bin"


class ConfigError(Exception):
    """config.yaml is missing, malformed, or fails validation."""


@functools.lru_cache(maxsize=1)
def load_config() -> dict:
    """Load and validate config.yaml. Path overridable via CP_AGENT_CONFIG."""
    path = Path(os.environ.get("CP_AGENT_CONFIG") or PROJECT_ROOT / "config.yaml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise ConfigError(
            f"找不到配置文件 {path}。请执行: cp config.yaml.example config.yaml 并填写 provider 配置"
        ) from None
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件 {path} 不是合法 YAML: {e}") from None
    _validate(cfg)
    return cfg


def _validate(cfg: dict) -> None:
    problems = []
    providers = cfg.get("providers") or {}
    if not providers:
        problems.append("providers 为空：至少配置一个 LLM provider")
    for protocol, group in providers.items():
        if not isinstance(group, dict):
            problems.append(f"providers.{protocol} 应为 provider 名到配置的映射")
            continue
        for name, pcfg in group.items():
            if not isinstance(pcfg, dict):
                problems.append(f"providers.{protocol}.{name} 应为映射")
                continue
            for field in ("base_url", "default_model"):
                if not pcfg.get(field):
                    problems.append(f"providers.{protocol}.{name} 缺少 {field}")
    now_model = cfg.get("nowModel") or cfg.get("now_model") or ""
    if now_model and _lookup_provider(providers, now_model) is None:
        problems.append(f"nowModel '{now_model}' 不在 providers 中")
    if not cfg.get("topics"):
        problems.append("topics 为空：至少配置一个算法主题")
    if problems:
        raise ConfigError("config.yaml 校验失败:\n  - " + "\n  - ".join(problems))


def _lookup_provider(providers: dict, name: str) -> tuple[str, dict] | None:
    """Find (protocol, cfg) by 'name' or 'protocol.name'; None if absent."""
    if "." in name:
        protocol, provider_name = name.split(".", 1)
        group = providers.get(protocol) or {}
        if provider_name in group:
            return protocol, group[provider_name]
        return None
    for protocol, group in providers.items():
        if name in group:
            return protocol, group[name]
    return None


# ─── Lazy config-derived attributes ──────────────────────────────────────────
_DERIVED = {
    "TESTLIB_PATH": lambda cfg: PROJECT_ROOT / cfg.get("testlib", "testlib.h"),
    "CXX": lambda cfg: os.environ.get("CXX", cfg.get("cxx", "g++")),
    "CXX_FLAGS": lambda cfg: cfg.get("cxx_flags", ["-std=c++17", "-O2", "-Wall", "-Wextra"]),
    "DEFAULT_TIME_LIMIT_MS": lambda cfg: cfg.get("time_limit_ms", 1000),
    "DEFAULT_MEMORY_LIMIT_MB": lambda cfg: cfg.get("memory_limit_mb", 256),
    "DIFFICULTY_PRESETS": lambda cfg: cfg.get("difficulty", {}),
    "DEFAULT_STRESS_ITERATIONS": lambda cfg: cfg.get("stress_iterations", 10000),
    "DEFAULT_STRESS_TIMEOUT_SEC": lambda cfg: cfg.get("stress_timeout_sec", 5),
    "DEFAULT_SOLUTION_TIMEOUT_SEC": lambda cfg: cfg.get("solution_timeout_sec", 5),
    "DEFAULT_STRESS_NAIVE_TIMEOUT_SEC": lambda cfg: cfg.get("stress_naive_timeout_sec", 15),
    "DEFAULT_STRESS_NAIVE_SOFT_LIMIT_SEC": lambda cfg: cfg.get("stress_naive_soft_limit_sec", 10),
    "DEDUP_JUDGE_TRIGGER": lambda cfg: cfg.get("dedup_judge_trigger", 0.5),
    "DEDUP_JUDGE_MAX_CANDIDATES": lambda cfg: cfg.get("dedup_judge_max_candidates", 5),
    "LLM_PROVIDERS": lambda cfg: cfg.get("providers", {}),
    "NOW_MODEL": lambda cfg: cfg.get("nowModel") or cfg.get("now_model") or "",
    "ALGO_TOPICS": lambda cfg: cfg.get("topics", {}),
}


def __getattr__(name: str):
    if name in _DERIVED:
        return _DERIVED[name](load_config())
    raise AttributeError(f"module 'config' has no attribute '{name}'")


# ─── Helper functions ────────────────────────────────────────────────────────

def _format_provider(protocol: str, name: str) -> str:
    return f"{protocol}.{name}"


def get_provider(name: str | None = None) -> tuple[str, dict]:
    """
    Look up a provider by name. Returns (protocol, config) or raises.

    Accepted names:
      - None / ""         → use config.yaml nowModel
      - "deepseek"        → legacy provider-name lookup
      - "openai.deepseek" → explicit protocol.provider lookup
    """
    cfg = load_config()
    name = name or cfg.get("nowModel") or cfg.get("now_model") or ""
    if not name:
        raise KeyError("No provider specified and config.yaml nowModel is empty")

    found = _lookup_provider(cfg.get("providers", {}), name)
    if found is None:
        raise KeyError(f"Unknown provider: '{name}'")
    protocol, pcfg = found
    if not pcfg.get("enabled", True):
        raise ValueError(f"Provider '{name}' is disabled in config.yaml")
    return protocol, pcfg


_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def resolve_api_key(provider_cfg: dict, cli_key: str | None = None,
                    provider_name: str = "") -> str:
    """
    Resolve the API key for a provider. Priority: CLI --api-key > api_key
    field (literal key) > env_key field (environment variable NAME only).
    """
    if cli_key:
        return cli_key
    api_key = provider_cfg.get("api_key") or ""
    if api_key:
        return api_key
    env_key = provider_cfg.get("env_key") or ""
    label = f"provider '{provider_name}' 的" if provider_name else ""
    if env_key:
        if not _ENV_VAR_RE.match(env_key):
            raise ConfigError(
                f"{label}env_key '{env_key[:8]}…' 看起来是明文 API key。"
                "env_key 只接受环境变量名（如 DEEPSEEK_API_KEY）；"
                "请把该值移到 api_key 字段，或删除并改用环境变量"
            )
        value = os.environ.get(env_key, "")
        if not value:
            raise ConfigError(f"{label}环境变量 {env_key} 未设置，且未配置 api_key")
        return value
    raise ConfigError(f"{label}API key 未配置（api_key / env_key / --api-key 均为空）")


def list_enabled_providers() -> dict[str, tuple[str, dict]]:
    """Return {name: (protocol, config)} for all enabled providers."""
    result = {}
    for protocol, providers in load_config().get("providers", {}).items():
        for name, cfg in providers.items():
            if cfg.get("enabled", True):
                result[name] = (protocol, cfg)
    return result


def list_enabled_provider_choices() -> list[str]:
    """Return provider names accepted by the CLI, including protocol-qualified aliases."""
    choices = []
    for protocol, providers in load_config().get("providers", {}).items():
        for name, cfg in providers.items():
            if cfg.get("enabled", True):
                choices.append(name)
                choices.append(_format_provider(protocol, name))
    return sorted(set(choices))


def get_now_model() -> str:
    """Return the configured default provider reference."""
    cfg = load_config()
    return cfg.get("nowModel") or cfg.get("now_model") or ""
