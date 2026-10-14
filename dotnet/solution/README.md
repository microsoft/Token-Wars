# Token Wars – .NET app (SOLUTION)

Reference implementation of the ByteCart "Support Copilot" for the **Token Wars: Build, Route & Optimize Your AI Stack**
microhack. It has the same folder layout as `dotnet/starter`, with every code TODO implemented (search for
`// SOLUTION x.y`). It also includes the optional **embedding retrieval** stretch goal (`"retrieval": "embedding"`).

It behaves exactly like the Python app in `python/solution` (same CLI, prompts, algorithms, JSON output).

## Prerequisites

- **.NET 8 SDK or newer** (`dotnet --version`). The project targets `net8.0` with `<RollForward>Major</RollForward>`, so it
  also runs on .NET 9/10.
- No NuGet packages: only `HttpClient`, `System.Text.Json`, `SemaphoreSlim` and `Task.WhenAll`.
- A deployed environment (`infra/` → generates `ROOT/.env` and `shared/config/models.json`), **or** mock mode (offline).

## Commands

Run everything from `dotnet/solution/TokenWars/` (the folder that contains `strategy.json`):

| Action | Command |
|---|---|
| Full scored run | `dotnet run -- run` |
| Quick run | `dotnet run -- run --limit 20` |
| Submit to leaderboard | `dotnet run -- run --submit` |
| Skip judging | `dotnet run -- run --no-judge` |
| Ask one question | `dotnet run -- ask "Can I return shoes?" --customer C1001` |
| Compare models (Ch.2) | `dotnet run -- compare --models premium,balanced,economy,open` |
| Offline mock mode | add `--mock` or set `TOKENWARS_MOCK=1` |
| Check setup | `dotnet run -- doctor` |

Extra option: `--strategy PATH` uses another strategy file.

The evaluator is the separate `judge` deployment: **GPT-5.5 (2026-04-24), capacity 800**, not Terra.
It calls Azure directly, bypasses APIM and uses `max_completion_tokens`, omitted temperature and
`reasoning_effort: none`. Prices are $5 / $0.50 / $30 per 1M input / cached input / output tokens;
`judge_cost_usd` remains outside the score. Challenges 1–3 and the ≥85% quality bar are unchanged.
Use the same pinned judge for every team and repeat baselines/comparisons after migration; old-judge
scores are not directly comparable. Terraform updates the generated registry only after deployment.

- `strategy.json` is read from the **current directory** (fallback: the project folder). Results are written to
  `results/run-<yyyyMMdd-HHmmss>.json` / `results/compare-<yyyyMMdd-HHmmss>.json` next to it.
- The repository root is found by walking up from the current directory to a folder containing `shared/config`
  (override with `TOKENWARS_ROOT`). `ROOT/.env` is parsed by the app; real environment variables win.
- Mock mode needs no Azure: answers are `[mock:<deployment>] …`, tokens are estimated from characters, embeddings are
  256-dim hashing vectors. If `shared/config/models.json` is missing it falls back to `models.example.json`, then to
  built-in defaults.
- Optional per-model fields in `models.json`: `supports_temperature` (default `true`; when `false` the app omits
  `temperature` from chat requests) and `extra_body` (default `{}`; merged into every chat request, e.g.
  `{"reasoning_effort": "none"}` for the GPT-5.6 family). Reasoning tokens are billed as output tokens and
  `max_completion_tokens` caps them too: if `max_output_tokens` is too low the model may return an **empty answer**
  (`finish_reason: "length"`), which is recorded as `""` (fails the judge) with a one-time warning.
- `extra_headers` (optional, default `{}`): a string → string map of HTTP headers added to every chat **and** embedding
  request for that model, e.g. `{"x-session-affinity": "bytecart"}`. Gateway credential overrides (`api-key`,
  `Authorization`, `Ocp-Apim-Subscription-Key`) are rejected; `Content-Type` is ignored.
  Mock mode ignores them; `doctor` shows the header names only.
- `hourly_cost_usd` (optional, default `0`): a fixed hosting fee, e.g. for a fine-tuned deployment. Informational only –
  when any model in a `compare` run has a value above 0, the table gets an extra `hosting $/h` column and each entry in
  the compare JSON gets an `hourly_cost_usd` field. It is never added to the costs or the score.
- **More model keys can appear.** With Challenge 2.4 `models.json` can also contain `fw` (a Fireworks
  model on Microsoft Foundry – see the website section [Challenge 2.4 Fireworks Arena](../../docs/index.html#step-2-4)),
  and the coach may add a `custom` entry (a fine-tuned model from the "Own the Weights" demo). Any key in
  `models.json` works for `compare --models balanced,premium,fw`, `default_model` and `Router.TierModels`;
  a key that is not configured fails before any spend. With escalation on, keys outside the explicit
  `economy → balanced → premium` chain (`open`, `selfhosted`, `fw`, `fw_*`, `custom`, …) escalate straight to `premium`.

**APIM is required for all real inference**, including Azure, Fireworks, Ollama and embeddings. Keep
`use_gateway: true` and every non-judge entry's `via_gateway: true`. All policies route by physical deployment
name; only the independent judge is direct. Missing gateway/key or disabled flags fail explicitly, without
direct fallback. Offline `--mock` still needs no gateway. External/custom resources need an APIM backend and
routing rule; changing `base_url` alone is insufficient. See [gateway and provider telemetry](../../README.md#required-gateway-and-provider-telemetry).

## Solution strategy (`TokenWars/strategy.json`)

```json
{
  "compact_prompt": true, "prompt_cache_friendly": true, "retrieval": "keyword", "top_k": 3,
  "order_lookup": true, "max_output_tokens": 350, "exact_cache": true, "semantic_cache": true,
  "semantic_cache_threshold": 0.92, "default_model": "balanced", "routing": "classifier", "escalation": true,
  "use_gateway": true, "retry_on_throttle": true, "concurrency": 8
}
```

## Lightweight self-hosted smoke test

The CPU-only Ollama deployment is not suitable for the expensive starter baseline: eight concurrent requests,
the entire knowledge base and orders database, and unlimited output can exceed the .NET client's 60-second timeout.
APIM also limits backend requests to 120 seconds, so raising only the client timeout is not a complete fix.

Use the separate [self-hosted strategy](TokenWars/strategy.selfhosted.json) from `dotnet/solution/TokenWars/`:

```powershell
dotnet run -- compare --models selfhosted --strategy strategy.selfhosted.json --limit 3 --no-judge
```

This profile uses the existing solution implementations: compact instructions, keyword retrieval of two sections,
relevant orders only, at most 96 output tokens, and concurrency 1. Routing, escalation and both response caches are
disabled so every answer tests Ollama through APIM without classifier or embedding calls. The default strategies and
starter exercises are unchanged. Do not use this profile in the starter until its corresponding TODOs are implemented.

`--no-judge` checks inference, not answer quality. Inspect the generated compare JSON for non-empty answers,
`error: null`, and latency below 60 seconds. Then remove `--no-judge` to evaluate quality with the pinned judge; a
96-token limit may truncate answers, and this small model is not guaranteed to meet the 85% pass-rate bar.
Increase the workload only after the smoke test works. If it still times out, check Ollama's logs for model loading,
queued requests or resource pressure before choosing a smaller model or changing infrastructure.

## Where each TODO is solved

| ID | Challenge | What | File |
|---|---|---|---|
| 1.1 | Token Diet | Compact system prompt | `TokenWars/Prompts.cs` (`CompactSystemPrompt`) |
| 1.2 | Token Diet | Keyword retrieval of top-k KB sections | `TokenWars/ContextBuilder.cs` (`KeywordRetrieval`) |
| 1.3 | Token Diet | Order lookup (only relevant orders) | `TokenWars/ContextBuilder.cs` (`LookupOrders`) |
| 1.4 | Token Diet | Output control (max tokens + low temperature) | `TokenWars/Pipeline.cs` (`GenerationSettings`) |
| 1.5 | Token Diet | Exact-match response cache | `TokenWars/ResponseCache.cs` (`ExactKey`, `GetExact`, `PutExact`) |
| 1.6 | Token Diet | Semantic cache (embeddings + cosine) | `TokenWars/ResponseCache.cs` (`CosineSimilarity`, `GetSemantic`, `PutSemantic`) |
| 1.7 | Token Diet | Prompt-caching-friendly message layout | `TokenWars/Prompts.cs` (`BuildCacheFriendlyMessages`) |
| 2.1 | BYOM | Deploy/register an open-weight model | `infra/`, `shared/config/models.json` |
| 2.2 | BYOM | Pick a model per tier after `compare` | `TokenWars/strategy.json` (`default_model`), `Router.TierModels` |
| 2.3 | BYOM | Model-specific prompt adaptation (`open`, `selfhosted`, `custom`, `fw`, `fw_*`) | `TokenWars/Prompts.cs` (`ModelSpecificInstructions`) |
| 3.1 | Route & Rule | Rules router | `TokenWars/Router.cs` (`RouteRules`) |
| 3.2 | Route & Rule | Classifier router (economy) | `TokenWars/Router.cs` (`RouteClassifierAsync`) |
| 3.3 | Route & Rule | Escalation on `ESCALATE` | `TokenWars/Pipeline.cs` (`AnswerCoreAsync`, step 8) |
| 3.4 | Route & Rule | Throttle-aware retry (429 + Retry-After) | `TokenWars/LlmClient.cs` (`SendWithRetryAsync`) |
| 3.5 | Route & Rule | Chat budgets, provider telemetry enrichment, pool/retry resilience | `infra/policies/ai-gateway-solution.xml` |
| stretch | Token Diet | Embedding retrieval (`"retrieval": "embedding"`) | `TokenWars/ContextBuilder.cs` (`EmbeddingRetrievalAsync`) |

## Files

```
TokenWars/TokenWars.csproj   net8.0, RollForward=Major, no packages
TokenWars/strategy.json      solution strategy
TokenWars/Program.cs         CLI (hand-rolled argument parsing)
TokenWars/Config.cs          ROOT/.env/models/pricing/scoring/strategy loading
TokenWars/LlmClient.cs       raw HTTP OpenAI-compatible client + mock mode + retry
TokenWars/Prompts.cs         baseline / compact / cache-friendly messages
TokenWars/ContextBuilder.cs  KB retrieval (all / keyword / embedding) + order lookup
TokenWars/ResponseCache.cs   exact + semantic cache
TokenWars/Router.cs          none / rules / classifier routing
TokenWars/Pricing.cs         token → USD
TokenWars/Judge.cs           LLM-as-a-judge
TokenWars/Pipeline.cs        per-item answer pipeline
TokenWars/Runner.cs          run / compare / ask / doctor, scorecard, results JSON, leaderboard submit
TokenWars/results/           run output
```
