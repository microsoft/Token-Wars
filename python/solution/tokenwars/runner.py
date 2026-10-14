"""Commands: run, compare, ask, doctor (+ leaderboard submit)."""
from __future__ import annotations

import json
import math
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests

from .config import AppConfig, ConfigError, Strategy
from .context import load_knowledge_base, load_orders, split_sections
from .judge import Judge
from .llm_client import LlmClient
from .pipeline import ItemResult, Pipeline

PREFLIGHT_ITEM = {"id": "preflight", "customer_id": "C1001", "question": "Can I return shoes I bought last week?"}
_print_lock = threading.Lock()


def _say(text: str = "") -> None:
    with _print_lock:
        print(text, flush=True)


# ---------------------------------------------------------------------- helpers


def load_workload(root: Path) -> list[dict[str, Any]]:
    path = root / "shared" / "data" / "workload.jsonl"
    if not path.exists():
        raise ConfigError(f"{path} not found")
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(json.loads(line))
    return items


def preflight(config: AppConfig, strategy: Strategy) -> None:
    """Runs one fake item fully offline so an enabled-but-unimplemented TODO fails fast, before any spend."""
    client = LlmClient(config, use_gateway=False, retry_on_throttle=strategy.retry_on_throttle, mock=True)
    Pipeline(config, strategy, client, persist_embeddings=False).answer(dict(PREFLIGHT_ITEM))


def utc_stamp() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y%m%d-%H%M%S")


def percentile(values: list[int], pct: float) -> int:
    """Nearest-rank percentile."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return int(ordered[rank - 1])


def fmt_usd(value: float) -> str:
    return f"${value:,.6f}" if value < 0.01 else f"${value:,.4f}"


def _mock_banner(config: AppConfig) -> None:
    if config.mock:
        _say("🧪 MOCK MODE – no Azure calls; answers are fake and costs are simulated from character counts.")
    for warning in config.warnings:
        _say(f"⚠️  {warning}")


def answer_all(pipeline: Pipeline, items: list[dict[str, Any]], concurrency: int,
               progress: bool = True) -> list[ItemResult]:
    """Answers items with `concurrency` workers; results stay in file order."""
    total = len(items)
    done = [0]

    def work(item: dict[str, Any]) -> ItemResult:
        # Only id / customer_id / question go into the pipeline (the rest is judge-only ground truth).
        result = pipeline.answer({"id": item["id"], "customer_id": item.get("customer_id", ""),
                                  "question": item["question"]})
        if progress:
            with _print_lock:
                done[0] += 1
                path = "→".join(result.model_path) or "-"
                line = (f"  [{done[0]:>3}/{total}] {result.id:<6} {path:<22} cache:{result.cache:<8} "
                        f"{result.input_tokens:>7} in {result.output_tokens:>5} out  {fmt_usd(result.cost_usd)}")
                if result.error:
                    line += f"  ❌ {result.error[:100]}"
                print(line, flush=True)
        return result

    executor = ThreadPoolExecutor(max_workers=max(1, concurrency))
    futures = [executor.submit(work, item) for item in items]
    try:
        results = [f.result() for f in futures]
    except BaseException:
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    executor.shutdown(wait=True)
    return results


def judge_all(config: AppConfig, results: list[ItemResult], workload: dict[str, dict[str, Any]],
              enabled: bool) -> float:
    """Fills judge_score / success on every item. Returns the judge cost (not part of the team score)."""
    pass_score = int(config.scoring.get("pass_score", 4))
    to_judge = []
    for result in results:
        if result.error:
            result.judge_score, result.judge_reason, result.success = 0, "error", False
        elif not enabled:
            result.judge_score, result.judge_reason, result.success = None, "not judged", False
        else:
            to_judge.append(result)
    if not to_judge:
        return 0.0

    concurrency = int(config.scoring.get("judge_concurrency", 8))
    _say(f"\n⚖️  Judging {len(to_judge)} answers (concurrency {concurrency}) ...")
    judge = Judge(config, LlmClient(config, use_gateway=False, retry_on_throttle=False))

    def work(result: ItemResult) -> float:
        truth = workload[result.id]
        verdict = judge.judge(truth["question"], truth.get("reference_answer", ""), truth.get("must_include", []),
                              result.answer, truth.get("customer_id", ""))
        result.judge_score, result.judge_reason = verdict.score, verdict.reason
        result.success = verdict.score >= pass_score
        return verdict.cost_usd

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        return sum(executor.map(work, to_judge))


def summarize(config: AppConfig, results: list[ItemResult], judge_cost: float) -> dict[str, Any]:
    items = len(results)
    successes = sum(1 for r in results if r.success)
    pass_rate = successes / items if items else 0.0
    total_cost = sum(r.cost_usd for r in results)
    latencies = [r.latency_ms for r in results]
    by_model: dict[str, dict[str, Any]] = {}
    for result in results:
        for call in result.calls:
            entry = by_model.setdefault(call.model_key, {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0,
                                                         "output_tokens": 0, "cost_usd": 0.0})
            entry["calls"] += 1
            entry["input_tokens"] += call.input_tokens
            entry["cached_input_tokens"] += call.cached_input_tokens
            entry["output_tokens"] += call.output_tokens
            entry["cost_usd"] += call.cost_usd
    for entry in by_model.values():
        entry["cost_usd"] = round(entry["cost_usd"], 8)
    return {
        "items": items,
        "successes": successes,
        "pass_rate": round(pass_rate, 4),
        "valid": pass_rate >= float(config.scoring.get("min_pass_rate", 0.85)),
        "total_cost_usd": round(total_cost, 8),
        "cost_per_success_usd": round(total_cost / max(successes, 1), 8),
        "input_tokens": sum(r.input_tokens for r in results),
        "cached_input_tokens": sum(r.cached_input_tokens for r in results),
        "output_tokens": sum(r.output_tokens for r in results),
        "cache_hits_exact": sum(1 for r in results if r.cache == "exact"),
        "cache_hits_semantic": sum(1 for r in results if r.cache == "semantic"),
        "escalations": sum(r.escalations for r in results),
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "judge_cost_usd": round(judge_cost, 8),
        "calls_by_model": dict(sorted(by_model.items())),
    }


def _display_width(text: str) -> int:
    return len(text) + sum(1 for ch in text if ch in "✅❌")


def print_scorecard(config: AppConfig, summary: dict[str, Any], judged: bool) -> None:
    width = 64
    min_rate = float(config.scoring.get("min_pass_rate", 0.85))
    if not judged:
        verdict = "NOT JUDGED (--no-judge) – pass rate unknown"
    elif summary["valid"]:
        verdict = "VALID ✅"
    else:
        verdict = f"INVALID ❌ (pass rate below {min_rate:.0%})"
    rows = [
        f"TOKEN WARS SCORECARD – {config.team} ({config.language}/{config.variant}){' [MOCK]' if config.mock else ''}",
        "",
        f"Items / successes      {summary['items']} / {summary['successes']}",
        f"Pass rate              {summary['pass_rate']:.1%}" if judged else "Pass rate              n/a (not judged)",
        f"Total cost             {fmt_usd(summary['total_cost_usd'])}",
        f"Cost per success       {fmt_usd(summary['cost_per_success_usd'])}",
        f"Input tokens           {summary['input_tokens']:,} ({summary['cached_input_tokens']:,} cached)",
        f"Output tokens          {summary['output_tokens']:,}",
        f"Cache hits             {summary['cache_hits_exact']} exact / {summary['cache_hits_semantic']} semantic",
        f"Escalations            {summary['escalations']}",
        f"Latency p50 / p95      {summary['latency_p50_ms']} ms / {summary['latency_p95_ms']} ms",
        f"Judge cost (unscored) {fmt_usd(summary['judge_cost_usd'])}",
        "",
        verdict,
    ]
    border = "+" + "-" * (width + 2) + "+"
    lines = [border]
    for row in rows:
        lines.append("| " + row + " " * max(width - _display_width(row), 0) + " |")
    lines.append(border)
    header = f"{'model':<12}{'calls':>7}{'input':>11}{'cached':>9}{'output':>9}{'cost':>14}"
    lines.append("| " + header.ljust(width) + " |")
    for key, entry in summary["calls_by_model"].items():
        row = (f"{key:<12}{entry['calls']:>7}{entry['input_tokens']:>11,}{entry['cached_input_tokens']:>9,}"
               f"{entry['output_tokens']:>9,}{fmt_usd(entry['cost_usd']):>14}")
        lines.append("| " + row.ljust(width) + " |")
    lines.append(border)
    _say("\n" + "\n".join(lines))


def write_json(config: AppConfig, prefix: str, payload: dict[str, Any], stamp: str) -> Path:
    path = config.results_dir / f"{prefix}-{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def submit(config: AppConfig, payload: dict[str, Any]) -> None:
    url = config.get_env("TOKENWARS_LEADERBOARD_URL").strip().rstrip("/")
    if not url:
        _say("⚠️  TOKENWARS_LEADERBOARD_URL is not set – skipping submit.")
        return
    headers = {"Content-Type": "application/json"}
    key = config.get_env("TOKENWARS_LEADERBOARD_KEY").strip()
    if key:
        headers["x-submit-key"] = key
    body = {k: payload[k] for k in ("team", "language", "variant", "timestamp", "mock", "strategy", "summary")}
    endpoint = f"{url}/api/submissions"
    try:
        response = requests.post(endpoint, data=json.dumps(body), headers=headers, timeout=30,
                                 allow_redirects=False)
        if response.status_code in (301, 302, 307, 308) and response.headers.get("location"):
            redirected = urljoin(endpoint, response.headers["location"])
            source, target = urlsplit(endpoint), urlsplit(redirected)
            same_origin = source.scheme == target.scheme and source.netloc == target.netloc
            https_upgrade = (source.scheme == "http" and target.scheme == "https"
                             and source.hostname == target.hostname
                             and source.port in (None, 80) and target.port in (None, 443))
            safe_redirect = same_origin or https_upgrade
            if not safe_redirect:
                _say(f"❌ Leaderboard refused unsafe redirect to {redirected}")
                return
            response = requests.post(redirected, data=json.dumps(body), headers=headers, timeout=30,
                                     allow_redirects=False)
        try:
            result = response.json()
        except requests.exceptions.JSONDecodeError:
            result = None
        if response.status_code != 201 or not isinstance(result, dict) or result.get("accepted") is not True:
            _say(f"❌ Leaderboard rejected the submission (HTTP {response.status_code}): {response.text[:500]}")
            return
        _say(f"📤 Leaderboard accepted the submission (HTTP 201): {response.text[:500]}")
    except requests.RequestException as exc:
        _say(f"❌ Submit failed: {exc}")


# ---------------------------------------------------------------------- commands


def cmd_run(config: AppConfig, strategy: Strategy, limit: int | None, judge_enabled: bool, do_submit: bool) -> int:
    _mock_banner(config)
    preflight(config, strategy)
    workload = load_workload(config.root)
    if limit is not None:
        workload = workload[:max(limit, 0)]
    _say(f"🏁 Running {len(workload)} items with strategy {config.strategy_path.name} "
         f"(default_model={strategy.default_model}, routing={strategy.routing}, retrieval={strategy.retrieval}, "
         f"concurrency={strategy.concurrency})")
    client = LlmClient(config, use_gateway=strategy.use_gateway, retry_on_throttle=strategy.retry_on_throttle)
    pipeline = Pipeline(config, strategy, client)
    results = answer_all(pipeline, workload, strategy.concurrency)
    judge_cost = judge_all(config, results, {w["id"]: w for w in workload}, judge_enabled)
    summary = summarize(config, results, judge_cost)
    print_scorecard(config, summary, judge_enabled)

    timestamp, stamp = utc_stamp()
    payload = {
        "schema_version": 1, "team": config.team, "language": config.language, "variant": config.variant,
        "timestamp": timestamp, "mock": config.mock, "strategy": strategy.to_dict(), "summary": summary,
        "items": [r.to_json() for r in results],
    }
    path = write_json(config, "run", payload, stamp)
    _say(f"💾 Results written to {path}")
    if do_submit:
        submit(config, payload)
    return 0


def cmd_compare(config: AppConfig, strategy: Strategy, models: list[str], judge_enabled: bool,
                limit: int | None = None) -> int:
    _mock_banner(config)
    workload = [w for w in load_workload(config.root) if w.get("compare")]
    if limit is not None:
        workload = workload[:max(limit, 0)]
    by_id = {w["id"]: w for w in workload}
    _say(f"🔬 Comparing {len(workload)} compare:true items across models: {', '.join(models)} "
         "(current prompt strategy; no routing, no caching, no escalation)")
    rows = []
    for key in models:
        model = config.models.get(key)
        if model is None or model.type != "chat":
            _say(f"⚠️  Skipping '{key}': not a configured chat model in {config.models_source}")
            continue
        model_strategy = strategy.copy(routing="none", default_model=key, exact_cache=False,
                                       semantic_cache=False, escalation=False)
        preflight(config, model_strategy)
        _say(f"\n▶ {key} ({model.deployment})")
        client = LlmClient(config, use_gateway=strategy.use_gateway, retry_on_throttle=strategy.retry_on_throttle)
        results = answer_all(Pipeline(config, model_strategy, client), workload, strategy.concurrency,
                             progress=False)
        judge_cost = judge_all(config, results, by_id, judge_enabled)
        summary = summarize(config, results, judge_cost)
        scores = [r.judge_score for r in results if r.judge_score is not None]
        summary["avg_score"] = round(sum(scores) / len(scores), 3) if scores else None
        summary["errors"] = sum(1 for r in results if r.error)
        rows.append({"model": key, "deployment": model.deployment, "hourly_cost_usd": model.hourly_cost_usd,
                     "summary": summary, "items": [r.to_json() for r in results]})

    # Optional fixed hosting fee (models.json "hourly_cost_usd", e.g. a fine-tuned deployment): informational only.
    show_hosting = any(row["hourly_cost_usd"] > 0 for row in rows)
    header = (f"{'model':<12}| {'pass rate':>9} | {'avg score':>9} | {'total cost':>12} | {'cost/success':>12} | "
              f"{'p50 ms':>7} | {'p95 ms':>7}")
    if show_hosting:
        header += f" | {'hosting $/h':>11}"
    lines = ["", header, "-" * len(header)]
    for row in rows:
        s = row["summary"]
        avg = f"{s['avg_score']:.2f}" if s["avg_score"] is not None else "n/a"
        rate = f"{s['pass_rate']:.0%}" if judge_enabled else "n/a"
        line = (f"{row['model']:<12}| {rate:>9} | {avg:>9} | {fmt_usd(s['total_cost_usd']):>12} | "
                f"{fmt_usd(s['cost_per_success_usd']):>12} | {s['latency_p50_ms']:>7} | {s['latency_p95_ms']:>7}")
        if show_hosting:
            hourly = row["hourly_cost_usd"]
            line += f" | {(f'${hourly:.2f}' if hourly > 0 else '-'):>11}"
        lines.append(line)
        if s["errors"]:
            lines.append(f"{'':<12}  ⚠️  {s['errors']} item(s) failed – see the JSON file for errors")
    if show_hosting:
        lines.append("hosting $/h = fixed hourly hosting fee from models.json (informational; not included in the "
                     "costs above or in the score)")
    _say("\n".join(lines))

    results_json = []
    for row in rows:
        entry = {"model": row["model"], "deployment": row["deployment"]}
        if show_hosting:
            entry["hourly_cost_usd"] = row["hourly_cost_usd"]
        entry["summary"] = row["summary"]
        entry["items"] = row["items"]
        results_json.append(entry)
    timestamp, stamp = utc_stamp()
    payload = {"schema_version": 1, "team": config.team, "language": config.language, "variant": config.variant,
               "timestamp": timestamp, "mock": config.mock, "strategy": strategy.to_dict(),
               "models": [row["model"] for row in rows], "results": results_json}
    path = write_json(config, "compare", payload, stamp)
    _say(f"\n💾 Results written to {path}")
    return 0


def cmd_ask(config: AppConfig, strategy: Strategy, question: str, customer_id: str) -> int:
    _mock_banner(config)
    preflight(config, strategy)
    client = LlmClient(config, use_gateway=strategy.use_gateway, retry_on_throttle=strategy.retry_on_throttle)
    result = Pipeline(config, strategy, client).answer({"id": "ask", "customer_id": customer_id,
                                                          "question": question})
    _say(f"\n❓ {question}  (customer {customer_id})\n")
    _say(result.answer if not result.error else f"❌ Error: {result.error}")
    _say("")
    _say(f"Model path : {' → '.join(result.model_path) or '-'}")
    _say(f"Cache      : {result.cache}")
    _say(f"Tokens     : {result.input_tokens:,} in ({result.cached_input_tokens:,} cached) / "
         f"{result.output_tokens:,} out")
    for call in result.calls:
        _say(f"             - {call.model_key}: {call.input_tokens:,} in / {call.output_tokens:,} out  "
             f"{fmt_usd(call.cost_usd)}")
    _say(f"Cost       : {fmt_usd(result.cost_usd)}")
    _say(f"Latency    : {result.latency_ms} ms")
    return 1 if result.error else 0


def cmd_doctor(config: AppConfig, strategy: Strategy) -> int:
    ok = True
    _say("🩺 Token Wars doctor")
    _say(f"  ROOT          : {config.root}")
    _say(f"  App           : {config.language}/{config.variant}  (strategy: {config.strategy_path})")
    _say(f"  Team          : {config.team}")
    _say(f"  Mock mode     : {'ON' if config.mock else 'off'}")
    _say(f"  Models from   : {config.models_source}")
    for warning in config.warnings:
        _say(f"  ⚠️  {warning}")

    root = config.root
    docs = load_knowledge_base(root)
    required = [
        (f"knowledge base: {len(docs)} docs / {len(split_sections(docs))} sections", bool(docs)),
        (f"data/orders.json: {len(load_orders(root))} orders", (root / "shared/data/orders.json").exists()),
        ("data/workload.jsonl", (root / "shared/data/workload.jsonl").exists()),
    ]
    optional = ["prompts/system-baseline.md", "prompts/judge.md", "config/pricing.json", "config/scoring.json"]
    _say("\n  Shared data:")
    for label, present in required:
        _say(f"    {'✅' if present else '❌'} {label}")
        ok &= present
    for name in optional:
        present = (root / "shared" / name).exists()
        _say(f"    {'✅' if present else '⚠️ '} {name}{'' if present else ' (missing – using a built-in fallback)'}")

    _say("\n  Models:")
    for key, model in config.models.items():
        key_env = model.api_key_env if key == config.scoring.get("judge_model", "judge") else (
            config.gateway.api_key_env if config.gateway else "APIM_SUBSCRIPTION_KEY")
        key_state = "key present" if config.get_env(key_env) else f"key MISSING ({key_env})"
        _say(f"    - {key:<11} {model.deployment:<26} {model.type:<9} via_gateway={str(model.via_gateway).lower():<5} "
             f"{key_state}")
        if model.type == "chat":
            extra = json.dumps(model.extra_body) if model.extra_body else "{}"
            details = (f"{model.max_tokens_param}, temperature={'yes' if model.supports_temperature else 'omitted'}, "
                       f"extra_body={extra}")
            if model.extra_headers:  # names only – values may be sensitive
                details += f", extra_headers: {', '.join(model.extra_headers)}"
            if model.hourly_cost_usd > 0:
                details += f", hosting ${model.hourly_cost_usd:.2f}/h"
            _say(f"      {'':<11} {details}")
    missing = [k for k in ("premium", "balanced", "economy", "embedding",
                          config.scoring.get("judge_model", "judge")) if k not in config.models]
    if missing:
        _say(f"    ⚠️  not configured: {', '.join(missing)}")
    if config.gateway:
        gw_key = "key present" if config.get_env(config.gateway.api_key_env) else \
            f"key MISSING ({config.gateway.api_key_env})"
        _say(f"    - gateway     {config.gateway.base_url}  {gw_key}  (use_gateway={str(strategy.use_gateway).lower()})")
    else:
        _say("    - gateway     REQUIRED (not configured; mock mode can run without it)")

    if config.mock:
        _say("\n  🧪 Mock mode – skipping test calls.")
        return 0 if ok else 1

    doctor_max_tokens = 64
    _say(f"\n  Test calls (chat output budget: {doctor_max_tokens} tokens each):")
    client = LlmClient(config, use_gateway=strategy.use_gateway, retry_on_throttle=False)
    for key, model in config.models.items():
        try:
            if model.type == "embedding":
                result = client.embed(key, ["ping"])
                detail = f"{len(result.vectors[0])} dims"
            else:
                result = client.chat(key, [{"role": "user", "content": "Reply with OK."}], max_tokens=doctor_max_tokens,
                                     purpose="doctor")
                detail = repr(result.text.strip())
            _say(f"    ✅ {key:<11} {result.latency_ms:>6} ms  {detail}")
        except Exception as exc:  # report every model, keep going
            ok = False
            _say(f"    ❌ {key:<11} {str(exc)[:160]}")
    return 0 if ok else 1
