# Token Wars – Python solution

This is the reference implementation of ByteCart's Support Copilot. It has the same structure as
`python/starter`, but every code TODO is implemented and marked with `# SOLUTION x.y`. It also includes the
embedding-retrieval stretch goal. `strategy.json` ships the optimised settings: compact and cache-friendly
prompts, keyword top-3 retrieval, order lookup, 350 max output tokens, exact and semantic caches, an economy
classifier router with `balanced` as the default, escalation and throttle-aware retry.

## Setup

Requires Python 3.10 or later. The only dependency is `requests`.

```bash
cd python/solution
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m tokenwars doctor
```

The app finds the repo root by walking up from the current folder until it sees `shared/config`. You can
override this with `TOKENWARS_ROOT`. It reads `ROOT/.env` and `ROOT/shared/config/models.json`, both of which
`terraform apply` generates. Real environment variables take precedence over `.env`.

## Commands

| Action | Command (run in `python/solution/`) |
|---|---|
| Full scored run | `python -m tokenwars run` |
| Quick run | `python -m tokenwars run --limit 20` |
| Submit to leaderboard | `python -m tokenwars run --submit` |
| Skip judging | `python -m tokenwars run --no-judge` |
| Ask one question | `python -m tokenwars ask "Can I return shoes?" --customer C1001` |
| Compare models (Ch.2) | `python -m tokenwars compare --models premium,balanced,economy,open` |
| Offline mock mode | add `--mock` or set `TOKENWARS_MOCK=1` |
| Check setup | `python -m tokenwars doctor` |

Other options: `--strategy PATH` uses a different strategy file, and `compare --no-judge` / `compare --limit N`
are also available.

Results go to `results/run-*.json` and `results/compare-*.json`. Embedding retrieval caches its section vectors
in `results/.kb-embeddings.json`, keyed by embedding deployment. In mock mode the key is prefixed with `mock:`.

## Models

`shared/config/models.json`, which terraform writes, maps the model keys the app uses to deployments:

| Key | Default deployment | Used for |
|---|---|---|
| `premium` | `gpt-5.6-sol` | the most capable (and most expensive) tier |
| `balanced` | `gpt-5.6-terra` | the standard tier |
| `economy` | `gpt-5.6-luna` | the cheapest tier and the routing classifier |
| `open` | `Llama-3.3-70B-Instruct` | open-weight model (Challenge 2) |
| `selfhosted` | e.g. `llama3.2:3b` on Ollama | optional self-hosted model (Challenge 2) |
| `embedding` | `text-embedding-3-small` | semantic cache / embedding retrieval |
| `judge` | `judge` (running `gpt-5.5`, version 2026-04-24) | independent scoring model, not part of your cost |

The separate GPT-5.5 judge has capacity 800, calls Azure directly (not APIM), and uses
`max_completion_tokens`, omitted temperature and `reasoning_effort: none`. Its $5 / $0.50 / $30 per 1M
input / cached input / output tokens are tracked in `judge_cost_usd`, outside the score. Challenges 1–3
and the ≥85% quality bar are unchanged. Keep the same pinned judge for every team; repeat baselines and
comparisons after migration rather than comparing against scores from the former Terra judge.

**APIM is required for all real inference**, including Azure, Fireworks, Ollama and embeddings. Keep
`use_gateway: true` and every non-judge entry's `via_gateway: true`. All policies route by physical deployment
name; only the independent judge is direct. Missing gateway/key or disabled flags fail explicitly, without
direct fallback. Offline `--mock` still needs no gateway. External/custom resources need an APIM backend and
routing rule; changing `base_url` alone is insufficient. See [gateway and provider telemetry](../../README.md#required-gateway-and-provider-telemetry).

Each model can also set these optional fields:

- `supports_temperature` (default `true`). When it is `false`, the app leaves `temperature` out of every chat request.
- `extra_body` (default `{}`). This object is merged into every chat request for that model.
- `extra_headers` (default `{}`). A string → string map of HTTP headers added to every chat **and** embedding request
  for that model, e.g. `{"x-session-affinity": "bytecart"}`. Gateway credential overrides (`api-key`,
  `Authorization`, `Ocp-Apim-Subscription-Key`) are rejected; `Content-Type` is ignored.
  Mock mode ignores them. `doctor` shows the header names only, not the values.
- `hourly_cost_usd` (default `0`). A fixed hosting fee, e.g. for a fine-tuned deployment. It is informational only:
  when any model in a `compare` run has a value above 0, the table gets an extra `hosting $/h` column and each entry
  in the compare JSON gets an `hourly_cost_usd` field. It is never added to the costs or the score.

The GPT-5.6 answer tiers and GPT-5.5 judge are configured with `"max_tokens_param": "max_completion_tokens"`, `"supports_temperature": false` and
`"extra_body": {"reasoning_effort": "none"}`.

**Reasoning tokens are part of your bill.** GPT-5.6 models can reason before they answer, and reasoning tokens are
billed as output tokens. Keeping `reasoning_effort` at `none` or `low` for the simple tiers is a real cost lever.
`max_completion_tokens` limits reasoning **and** answer tokens together. If `max_output_tokens` is too low, the model
can use up the whole budget on reasoning and return an **empty answer** (`finish_reason: "length"`). The app records
it as `""` (which fails the judge) and prints a one-time warning.

`pricing.json` includes prices for the `gpt-5.6-*` answer models and `gpt-5.5` judge as well as the older `gpt-4.1*` models, for teams that
still have access to those.

**More model keys can appear.** With Challenge 2.4 the generated `models.json` can also contain `fw`
(a Fireworks model on Microsoft Foundry – see the website section
[Challenge 2.4 Fireworks Arena](../../docs/index.html#step-2-4)), and the coach may add a `custom` entry (a fine-tuned
model from the "Own the Weights" demo). Any key in `models.json` works everywhere: `compare --models
balanced,premium,fw`, `default_model` and `TIER_MODELS` in `tokenwars/router.py`. A key that is not configured fails before any spend.
With escalation on, keys outside the explicit `economy → balanced → premium` chain (`open`, `selfhosted`, `fw`, `fw_*`,
`custom`, …) escalate straight to `premium`.

## Where each TODO is solved

| ID | What | File | Flag in `strategy.json` |
|---|---|---|---|
| 1.1 | Compact system prompt | `tokenwars/prompts.py` (`COMPACT_SYSTEM_PROMPT`) | `compact_prompt` |
| 1.2 | Keyword retrieval (idf = ln(1 + N/df), top-k) | `tokenwars/context.py` (`keyword_retrieval`) | `retrieval: "keyword"`, `top_k` |
| 1.3 | Order lookup | `tokenwars/context.py` (`lookup_orders`) | `order_lookup` |
| 1.4 | Output control (max tokens + temperature 0.2 where supported) | `tokenwars/pipeline.py` (`generation_settings`) | `max_output_tokens` |
| 1.5 | Exact-match cache | `tokenwars/cache.py` | `exact_cache` |
| 1.6 | Semantic cache | `tokenwars/cache.py` | `semantic_cache`, `semantic_cache_threshold` |
| 1.7 | Prompt-caching-friendly layout | `tokenwars/prompts.py` (`build_cache_friendly_messages`) | `prompt_cache_friendly` |
| 2.2 | Model per tier | `strategy.json` `default_model`, `TIER_MODELS` in `tokenwars/router.py` | `default_model` |
| 2.3 | Prompt adaptation for non-OpenAI models (`open`, `selfhosted`, `custom`, `fw_*`) | `tokenwars/prompts.py` (`model_specific_instructions`) | – |
| 3.1 | Rules router | `tokenwars/router.py` (`route_rules`) | `routing: "rules"` |
| 3.2 | Classifier router (economy) | `tokenwars/router.py` (`route_classifier`) | `routing: "classifier"` |
| 3.3 | Escalation on `ESCALATE` (unmapped keys such as `fw_*` / `custom` → `premium`) | `tokenwars/pipeline.py` | `escalation` |
| 3.4 | Throttle-aware retry | `tokenwars/llm_client.py` (`_send_with_retry`) | `retry_on_throttle` |
| 3.5 | Chat budgets, provider telemetry enrichment, pool/retry resilience | `infra/policies/ai-gateway-solution.xml` | gateway already required |
| stretch | Embedding retrieval | `tokenwars/context.py` (`embedding_retrieval`) | `retrieval: "embedding"` |

## Notes

- In mock mode the classifier always answers `SIMPLE`, so every uncached item goes to `economy`, and no escalations
  happen. Use a real run to see the full routing mix.
- The judge retries throttled calls by itself, so your score never depends on TODO 3.4. Judge cost is reported
  separately and is not part of the team score.
- `compare` runs each chosen inference model through APIM with the current prompt strategy, without tier routing,
  caching or escalation. Provider routing remains built into the gateway; only the judge is direct.
