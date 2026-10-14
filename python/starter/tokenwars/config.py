"""Configuration: locate ROOT, read .env, models.json, pricing.json, scoring.json and strategy.json."""
from __future__ import annotations

import copy
import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

VARIANT = "starter"
LANGUAGE = "python"

APP_DIR = Path(__file__).resolve().parent.parent  # python/<variant>/

# "Today" for the ByteCart scenario (used in the prompt-cache-friendly layout).
SCENARIO_DATE = "2026-09-15"

MODEL_KEYS = ["premium", "balanced", "economy", "open", "selfhosted", "embedding", "judge"]

_MODEL_DEFAULTS = {
    "base_url": "",
    "api_key_env": "AZURE_AI_API_KEY",
    "type": "chat",
    "via_gateway": True,
    "max_tokens_param": "max_tokens",
    "supports_temperature": True,
    "extra_body": {},
    "extra_headers": {},
    "hourly_cost_usd": 0.0,
}

# GPT-5.6 family (reasoning-capable): no temperature, max_completion_tokens, reasoning effort "none".
_GPT56 = {"via_gateway": True, "max_tokens_param": "max_completion_tokens", "supports_temperature": False,
          "extra_body": {"reasoning_effort": "none"}}

# Used only in mock mode when neither models.json nor models.example.json exists.
_BUILTIN_MODELS = {
    "models": {
        "premium": {"deployment": "gpt-5.6-sol", "pricing_key": "gpt-5.6-sol", **_GPT56},
        "balanced": {"deployment": "gpt-5.6-terra", "pricing_key": "gpt-5.6-terra", **_GPT56},
        "economy": {"deployment": "gpt-5.6-luna", "pricing_key": "gpt-5.6-luna", **_GPT56},
        "open": {"deployment": "Llama-3.3-70B-Instruct", "pricing_key": "llama-3.3-70b-instruct", "via_gateway": True},
        "embedding": {"deployment": "text-embedding-3-small", "pricing_key": "text-embedding-3-small", "type": "embedding"},
        "judge": {"deployment": "judge", "pricing_key": "gpt-5.5", "via_gateway": False,
                  "max_tokens_param": "max_completion_tokens", "supports_temperature": False,
                  "extra_body": {"reasoning_effort": "none"}},
    },
    "gateway": None,
}

# Standard Global short-context snapshot; Sol input/output promotion runs through at least 2026-11-30.
_BUILTIN_PRICING = {
    "currency": "USD",
    "models": {
        "gpt-5.5": {"input_per_1m": 5.00, "cached_input_per_1m": 0.50, "output_per_1m": 30.00},
        "gpt-5.6-sol": {"input_per_1m": 4.00, "cached_input_per_1m": 0.50, "output_per_1m": 20.00},
        "gpt-5.6-terra": {"input_per_1m": 2.00, "cached_input_per_1m": 0.20, "output_per_1m": 12.00},
        "gpt-5.6-luna": {"input_per_1m": 0.20, "cached_input_per_1m": 0.02, "output_per_1m": 1.20},
        "gpt-5.4": {"input_per_1m": 2.50, "cached_input_per_1m": 0.25, "output_per_1m": 15.00},
        "gpt-5.4-mini": {"input_per_1m": 0.75, "cached_input_per_1m": 0.075, "output_per_1m": 4.50},
        "gpt-5.4-nano": {"input_per_1m": 0.20, "cached_input_per_1m": 0.02, "output_per_1m": 1.25},
        "gpt-4.1": {"input_per_1m": 2.00, "cached_input_per_1m": 0.50, "output_per_1m": 8.00},
        "gpt-4.1-mini": {"input_per_1m": 0.40, "cached_input_per_1m": 0.10, "output_per_1m": 1.60},
        "gpt-4.1-nano": {"input_per_1m": 0.10, "cached_input_per_1m": 0.025, "output_per_1m": 0.40},
        "llama-3.3-70b-instruct": {"input_per_1m": 0.71, "cached_input_per_1m": 0.71, "output_per_1m": 0.71},
        "selfhosted": {"input_per_1m": 0.0, "cached_input_per_1m": 0.0, "output_per_1m": 0.0},
        "text-embedding-3-small": {"input_per_1m": 0.02, "cached_input_per_1m": 0.02, "output_per_1m": 0.0},
    },
}

_BUILTIN_SCORING = {"pass_score": 4, "min_pass_rate": 0.85, "judge_model": "judge", "judge_concurrency": 8}


class ConfigError(Exception):
    """Raised when the setup is incomplete (missing files, unknown model keys, ...)."""


# --------------------------------------------------------------------------- strategy


@dataclass
class Strategy:
    compact_prompt: bool = False
    prompt_cache_friendly: bool = False
    retrieval: str = "all"  # all | keyword | embedding
    top_k: int = 3
    order_lookup: bool = False
    max_output_tokens: int | None = None
    exact_cache: bool = False
    semantic_cache: bool = False
    semantic_cache_threshold: float = 0.92
    default_model: str = "premium"
    routing: str = "none"  # none | rules | classifier
    escalation: bool = False
    use_gateway: bool = True
    retry_on_throttle: bool = False
    concurrency: int = 8

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def copy(self, **changes: Any) -> "Strategy":
        data = self.to_dict()
        data.update(changes)
        return Strategy(**data)


def load_strategy(path: Path) -> Strategy:
    if not path.exists():
        raise ConfigError(f"strategy.json not found at {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"strategy.json is not valid JSON: {exc}") from exc
    known = {f.name for f in fields(Strategy)}
    unknown = sorted(set(raw) - known)
    if unknown:
        print(f"⚠️  Ignoring unknown strategy.json keys: {', '.join(unknown)}", file=sys.stderr)
    strategy = Strategy(**{k: v for k, v in raw.items() if k in known})
    if strategy.use_gateway is not True:
        raise ConfigError("APIM is required: strategy.use_gateway must be true (the judge is the only direct exception).")
    if strategy.retrieval not in ("all", "keyword", "embedding"):
        raise ConfigError(f'strategy.retrieval must be "all", "keyword" or "embedding" (got "{strategy.retrieval}")')
    if strategy.routing not in ("none", "rules", "classifier"):
        raise ConfigError(f'strategy.routing must be "none", "rules" or "classifier" (got "{strategy.routing}")')
    strategy.top_k = max(1, int(strategy.top_k))
    strategy.concurrency = max(1, int(strategy.concurrency))
    if strategy.max_output_tokens is not None:
        strategy.max_output_tokens = int(strategy.max_output_tokens)
    return strategy


def find_strategy_path(explicit: str | None = None) -> Path:
    """strategy.json from --strategy, else the current directory, else python/<variant>/."""
    if explicit:
        return Path(explicit).resolve()
    cwd_candidate = Path.cwd() / "strategy.json"
    if cwd_candidate.exists():
        return cwd_candidate.resolve()
    return APP_DIR / "strategy.json"


# --------------------------------------------------------------------------- root / env


def find_root() -> Path:
    override = os.environ.get("TOKENWARS_ROOT")
    if override:
        root = Path(override).expanduser().resolve()
        if not (root / "shared" / "config").is_dir():
            raise ConfigError(f"TOKENWARS_ROOT={override} does not contain shared/config")
        return root
    for start in (Path.cwd().resolve(), APP_DIR):
        for folder in (start, *start.parents):
            if (folder / "shared" / "config").is_dir():
                return folder
    raise ConfigError("Could not find the repo root (a folder containing shared/config). "
                      "Run from inside the repo or set TOKENWARS_ROOT.")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        elif value.startswith("#"):
            value = ""
        values[key] = value
    return values


# --------------------------------------------------------------------------- models


@dataclass
class ModelConfig:
    key: str
    deployment: str
    base_url: str
    api_key_env: str
    pricing_key: str
    type: str = "chat"
    via_gateway: bool = True
    max_tokens_param: str = "max_tokens"
    supports_temperature: bool = True
    extra_body: dict[str, Any] = field(default_factory=dict)
    # Extra HTTP headers for every chat/embedding request (e.g. {"x-session-affinity": "bytecart"}).
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Informational hosting fee (e.g. a fine-tuned deployment); shown by `compare`, never part of the score.
    hourly_cost_usd: float = 0.0


@dataclass
class GatewayConfig:
    base_url: str
    api_key_env: str


@dataclass
class AppConfig:
    root: Path
    app_dir: Path
    strategy_path: Path
    env: dict[str, str]
    mock: bool
    models: dict[str, ModelConfig]
    models_source: str
    gateway: GatewayConfig | None
    pricing: dict[str, Any]
    scoring: dict[str, Any]
    team: str
    variant: str = VARIANT
    language: str = LANGUAGE
    warnings: list[str] = field(default_factory=list)

    @property
    def results_dir(self) -> Path:
        path = self.app_dir / "results"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_env(self, name: str, default: str = "") -> str:
        return self.env.get(name, default)

    def model(self, key: str) -> ModelConfig:
        if key not in self.models:
            raise ConfigError(f'Model "{key}" is not configured in {self.models_source}')
        return self.models[key]

    def chat_models(self) -> list[str]:
        return [k for k, m in self.models.items() if m.type == "chat"]


def _normalise_base_url(url: str) -> str:
    url = (url or "").strip()
    return url if not url or url.endswith("/") else url + "/"


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _string_map(value: Any) -> dict[str, str]:
    """JSON object -> {name: text}; non-string values are kept as compact JSON, nulls are dropped."""
    if not isinstance(value, dict):
        return {}
    return {str(k): v if isinstance(v, str) else json.dumps(v, separators=(",", ":"), ensure_ascii=False)
            for k, v in value.items() if v is not None}


def _parse_models(raw: dict[str, Any]) -> tuple[dict[str, ModelConfig], GatewayConfig | None]:
    models: dict[str, ModelConfig] = {}
    for key, entry in (raw.get("models") or {}).items():
        if not isinstance(entry, dict) or not entry.get("deployment"):
            continue
        if "via_gateway" in entry and not isinstance(entry["via_gateway"], bool):
            raise ConfigError(f'Model "{key}": via_gateway must be a JSON boolean.')
        merged = {**_MODEL_DEFAULTS, **entry}
        models[key] = ModelConfig(
            key=key,
            deployment=str(merged["deployment"]),
            base_url=_normalise_base_url(merged.get("base_url", "")),
            api_key_env=str(merged.get("api_key_env") or "AZURE_AI_API_KEY"),
            pricing_key=str(merged.get("pricing_key") or merged["deployment"]),
            type=str(merged.get("type") or "chat"),
            via_gateway=_as_bool(entry.get("via_gateway"), key != "judge"),
            max_tokens_param=str(merged.get("max_tokens_param") or "max_tokens"),
            supports_temperature=_as_bool(merged.get("supports_temperature"), True),
            extra_body=dict(merged.get("extra_body") or {}) if isinstance(merged.get("extra_body"), dict) else {},
            extra_headers=_string_map(merged.get("extra_headers")),
            hourly_cost_usd=_as_float(merged.get("hourly_cost_usd")),
        )
    gateway = None
    gw = raw.get("gateway")
    if isinstance(gw, dict) and gw.get("base_url"):
        gateway = GatewayConfig(base_url=_normalise_base_url(gw["base_url"]),
                                api_key_env=str(gw.get("api_key_env") or "APIM_SUBSCRIPTION_KEY"))
    return models, gateway


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc


def load_config(mock_flag: bool = False, strategy_path: Path | None = None) -> AppConfig:
    root = find_root()
    env = parse_env_file(root / ".env")
    env.update(os.environ)  # real environment variables override .env
    mock = mock_flag or env.get("TOKENWARS_MOCK", "0").strip().lower() in ("1", "true", "yes", "on")
    warnings: list[str] = []

    config_dir = root / "shared" / "config"
    models_path = config_dir / "models.json"
    example_path = config_dir / "models.example.json"
    if models_path.exists():
        raw_models, source = _read_json(models_path), str(models_path)
    elif mock and example_path.exists():
        raw_models, source = _read_json(example_path), str(example_path)
        warnings.append("models.json not found – mock mode uses models.example.json")
    elif mock:
        raw_models, source = copy.deepcopy(_BUILTIN_MODELS), "built-in mock defaults"
        warnings.append("models.json not found – mock mode uses built-in model defaults")
    else:
        raise ConfigError(f"{models_path} not found. Deploy the infra (terraform apply) "
                          "or run offline with --mock / TOKENWARS_MOCK=1.")
    models, gateway = _parse_models(raw_models)

    pricing_path = config_dir / "pricing.json"
    if pricing_path.exists():
        pricing = _read_json(pricing_path)
    else:
        pricing = copy.deepcopy(_BUILTIN_PRICING)
        warnings.append("pricing.json not found – using built-in list prices")

    scoring_path = config_dir / "scoring.json"
    scoring = dict(_BUILTIN_SCORING)
    if scoring_path.exists():
        scoring.update(_read_json(scoring_path))
    else:
        warnings.append("scoring.json not found – using built-in scoring defaults")

    strategy_path = strategy_path or find_strategy_path()
    return AppConfig(
        root=root,
        app_dir=strategy_path.parent,
        strategy_path=strategy_path,
        env=env,
        mock=mock,
        models=models,
        models_source=source,
        gateway=gateway,
        pricing=pricing,
        scoring=scoring,
        team=env.get("TOKENWARS_TEAM", "").strip() or "anonymous",
        warnings=warnings,
    )
