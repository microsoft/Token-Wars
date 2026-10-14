# Token Wars – Python starter

This is the **expensive baseline** of ByteCart's Support Copilot. It works end to end out of the box, and it is
wasteful on purpose. It always calls the premium model, puts the whole knowledge base and the whole orders
database into every prompt, starts the system prompt with a timestamp (so prompt caching never works), sets no
output limit and does no caching.

Your job is to lower the **cost per successful answer** and keep the pass rate at **≥ 85 %**. To do that, you
implement the `# TODO x.y` blocks and switch on the matching flags in `strategy.json`.

## Setup

Requires Python 3.10 or later. The only dependency is `requests`.

```bash
cd python/starter
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m tokenwars doctor             # checks ROOT, models, keys and test calls (64-token chat output budget)
```

The app finds the repo root by walking up from the current folder until it sees `shared/config`. You can
override this with `TOKENWARS_ROOT`. It reads `ROOT/.env` and `ROOT/shared/config/models.json`, both of which
`terraform apply` generates. Real environment variables take precedence over `.env`.

## Commands

| Action | Command (run in `python/starter/`) |
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

Each run prints a scorecard and writes `results/run-<yyyyMMdd-HHmmss>.json`. `compare` writes
`results/compare-<yyyyMMdd-HHmmss>.json`.

**Mock mode** makes no Azure calls. Answers are fake (`[mock:<deployment>] …`), token counts are estimated as
characters / 4, the judge always gives 5 and the classifier always returns `SIMPLE`. Use it to test your code
offline and to compare token counts. Mock runs cannot go on the leaderboard.

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

## Where the TODOs live

When you turn on a flag in `strategy.json` before implementing its TODO, the app stops right away, before
spending anything, with `NotImplementedError: TODO x.y not implemented yet – see <file>`.

| ID | Challenge | What | File | Flag in `strategy.json` |
|---|---|---|---|---|
| 1.1 | Token Diet | Compact system prompt | `tokenwars/prompts.py` | `"compact_prompt": true` |
| 1.2 | Token Diet | Keyword retrieval of top-k KB sections | `tokenwars/context.py` | `"retrieval": "keyword"`, `"top_k"` |
| 1.3 | Token Diet | Order lookup (only relevant orders) | `tokenwars/context.py` | `"order_lookup": true` |
| 1.4 | Token Diet | Output control (max tokens, temperature 0.2 where supported) | `tokenwars/pipeline.py` | `"max_output_tokens": 350` |
| 1.5 | Token Diet | Exact-match response cache | `tokenwars/cache.py` | `"exact_cache": true` |
| 1.6 | Token Diet | Semantic cache (embeddings + cosine) | `tokenwars/cache.py` | `"semantic_cache": true`, `"semantic_cache_threshold"` |
| 1.7 | Token Diet | Prompt-caching-friendly message layout | `tokenwars/prompts.py` | `"prompt_cache_friendly": true` |
| 2.1 | BYOM | Deploy/register an open-weight model | `infra/`, `shared/config/models.json` | – |
| 2.2 | BYOM | Run `compare`, pick a model per tier | `strategy.json` (`default_model`), `TIER_MODELS` in `tokenwars/router.py` | `"default_model"` |
| 2.3 | BYOM | Extra instruction for non-OpenAI models (`open`, `selfhosted`, `custom`, `fw`, `fw_*`) | `tokenwars/prompts.py` | – (always active once implemented) |
| 3.1 | Route & Rule | Rules router | `tokenwars/router.py` | `"routing": "rules"` |
| 3.2 | Route & Rule | Classifier router (economy) | `tokenwars/router.py` | `"routing": "classifier"` |
| 3.3 | Route & Rule | Escalation on `ESCALATE` | `tokenwars/pipeline.py` | `"escalation": true` |
| 3.4 | Route & Rule | Throttle-aware retry (429 + Retry-After) | `tokenwars/llm_client.py` | `"retry_on_throttle": true` |
| 3.5 | Route & Rule | Add chat budgets, enrich existing provider metrics, add pool/retry resilience | `infra/policies/ai-gateway-starter.xml` | gateway already required |
| stretch | – | Embedding retrieval | `tokenwars/context.py` | `"retrieval": "embedding"` |

To find every task, search for `TODO` in `tokenwars/`.

## How the pipeline works (`tokenwars/pipeline.py`)

For each workload item, the pipeline only receives `id`, `customer_id` and `question`. Everything else in the
workload is ground truth for the judge, and using it for routing means disqualification.

1. Normalise the question and detect whether it is about orders.
2. Exact cache, then semantic cache (semantic only for questions that are not about orders).
3. Route to a model: `default_model`, rules or the classifier.
4. Build the context (KB retrieval and orders), build the messages and call the model.
5. Escalate to the next tier (`economy → balanced → premium`; any other key, e.g. `open`, `selfhosted`, `fw`, `custom`
   `→ premium`) when the model answers `ESCALATE`.
6. Store the answer in the caches. Every call's tokens are priced with `shared/config/pricing.json`.

## Files

```
tokenwars/__main__.py   CLI (argparse)
tokenwars/config.py     ROOT/.env/models/pricing/scoring/strategy loading, VARIANT = "starter"
tokenwars/llm_client.py raw-HTTP OpenAI-compatible client + mock mode        (TODO 3.4)
tokenwars/prompts.py    baseline / compact / cache-friendly prompts          (TODO 1.1, 1.7, 2.3)
tokenwars/context.py    KB retrieval + order lookup                          (TODO 1.2, 1.3)
tokenwars/cache.py      exact + semantic response cache                      (TODO 1.5, 1.6)
tokenwars/router.py     rules + classifier routing                           (TODO 3.1, 3.2)
tokenwars/pipeline.py   answer pipeline                                      (TODO 1.4, 3.3)
tokenwars/pricing.py    token → USD
tokenwars/judge.py      LLM-as-a-judge (cost tracked separately, not part of your score)
tokenwars/runner.py     run / compare / ask / doctor / submit, scorecard
```
