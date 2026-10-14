# Scoring Rules — Token Wars

## The metric

```
cost_per_success_usd = total_cost_usd / max(successes, 1)
```

- **total_cost_usd** = sum over every model call made while answering the 100 workload questions:
  `((prompt_tokens − cached_tokens) × input_price + cached_tokens × cached_input_price + completion_tokens × output_price) / 1,000,000`
  with prices from `shared/config/pricing.json` (USD per 1M tokens, keyed by the model's `pricing_key`).
  This includes classifier calls, escalations, embeddings (semantic cache / embedding retrieval) and retries that
  returned usage. Reasoning tokens are part of `completion_tokens` and are therefore charged at the output price. Calls through the APIM gateway are priced with the underlying model's pricing key.
- **successes** = questions whose judge score is ≥ `pass_score` (4 of 5, `shared/config/scoring.json`).
  Items that errored count as score 0 / not successful. `--no-judge` runs have no successes.
- Cache hits cost $0 (the answer is reused) — except the embedding call needed to check the semantic cache.
- Self-hosted models are priced at $0 per token (infra cost is discussed in the debrief, not scored).

## Fireworks and custom models

- **The Fireworks model (`fw`, Challenge 2.4) and any custom model are scored like every other model:**
  per-token prices from `shared/config/pricing.json` via the model's `pricing_key` (`fw-glm-5.3-flash`),
  with the same formula, validity gate and ranking.
- **Unverified prices are flagged:** entries with `"verified": false` in `pricing.json` (currently the Fireworks prices)
  are illustrative. The coach confirms or updates them one week before the event and announces at kickoff which
  prices are still unverified. They are identical for every team, so the ranking stays comparable; prices are not
  changed during the event.
- **Hourly hosting fees are informational only.** A model's `hourly_cost_usd` (e.g. a fine-tuned deployment) shows up
  as the *hosting $/h* column in `compare` but is **never** added to `total_cost_usd` or the score. Fireworks
  pay-per-token deployments have no hourly fee.
- A team that wants to use another model from its own subscription needs a `pricing_key` with a realistic per-token
  price that the coach adds to the shared `pricing.json` (see fair play). PTU-priced models are not allowed.
- **Coach demo models are not eligible for the leaderboard.** The `custom` model from the "Own the Weights" demo runs
  in the coach subscription, its price is only written to the coach's local `pricing.json`, and demo runs are never
  submitted.
- **Required gateway:** all inference (including Fireworks, Ollama and embeddings) uses APIM; only the independent
  judge is direct. Built-in provider routing does not change ordinary model pricing.
- **Multi-provider failover (Challenge 3.6):** the app does not use the gateway's provider/model response headers
  to reprice calls. A failed-over answer therefore uses the **requested** model's `pricing_key`, not the serving
  Fireworks price. The leaderboard is not exact provider billing. Quality drift does affect the judge score.
  Use outbound **Provider Tokens** grouped by lowercase `team`/`provider` for the serving-provider split;
  do not sum it with native requested-route LLM metrics. Failed attempts without reported usage remain uncounted.

## Validity gate

A run is **valid** only if:
1. it covers the **full workload** (100 items, no `--limit`),
2. the **pass rate ≥ 85 %** (`min_pass_rate`),
3. it is a **real run** (not `--mock` / `TOKENWARS_MOCK=1`; the leaderboard refuses mock runs).

Invalid runs are shown below the ranking but never rank.

## Ranking

Each team is ranked by its **best valid submission**:
1. lowest `cost_per_success_usd`,
2. tie-breaker: higher pass rate,
3. tie-breaker: lower p95 latency (`latency_p95_ms`),
4. tie-breaker: earlier submission.

The leaderboard also shows total cost, cache hits, number of submissions and the improvement versus the team's first
submission (usually the baseline) — there is a special mention for the **biggest improvement**.

## The judge

- Model: the separate `judge` deployment (gpt-5.5 version 2026-04-24, GlobalStandard, capacity 800,
  `reasoning_effort: none`; temperature 0 only for models with
  `supports_temperature: true`), JSON output, prompt `shared/prompts/judge.md`.
- It compares the answer with a reference answer and 1–3 `must_include` facts:
  5 = correct & complete · 4 = correct, minor omissions, all must-include facts present · 3 = partially correct or a
  must-include fact missing · 2 = mostly wrong, or only "I'll escalate to a human" · 1 = wrong/hallucinated/harmful.
- Verbosity is neither rewarded nor penalised.
- **Judge cost is tracked separately (`judge_cost_usd`) and is NOT part of the score.**
- The evaluator is distinct from the GPT-5.6 answer tiers. It bypasses APIM and is not a failover target.
  Challenge 1 optimisations, Challenge 2 comparisons and Challenge 3 routing/escalation keep the same rubric
  and ≥85% pass-rate requirement. Model separation does not guarantee freedom from grading bias.
- Coaches pin the same judge version, prompt and settings for all teams; validate a sample against human grades
  before the event. After migrating from Terra, repeat baselines and comparisons. Do not mix old-judge and
  new-judge scores in the same ranking or claim a quality improvement solely from the evaluator change.
- GPT-5.5 rates are $5 input / $0.50 cached input / $30 output per 1M tokens
  ([Microsoft source](https://azure.microsoft.com/en-us/blog/openais-gpt-5-5-in-microsoft-foundry-frontier-intelligence-on-an-enterprise-ready-platform/)).
  Measure actual judge token use and latency in the dry run; there is no extra answer-cost score penalty.
- The judge is an LLM: expect ±2–3 % pass-rate noise between identical runs. Re-running to get lucky is allowed but
  costs time; coaches may ask a team to re-run if their best run is a clear outlier.

## Fair play (disqualification)

- The answering pipeline may only use `id`, `customer_id` and `question` from `workload.jsonl`.
  Using `category`, `difficulty`, `reference_answer`, `must_include` or `compare` for answering, routing, caching or
  prompt building = **disqualification**.
- No hard-coding answers, question IDs or question text (e.g. a lookup table of the 100 questions); caches must be
  built at run time from the run itself. Pre-computing KB section embeddings is fine.
- Do not modify the judge (prompt, model, pass score), `pricing.json`, `scoring.json` or the workload, and do not
  change the cost accounting in the app. Coaches may diff these files against the repository.
- Do not route calls to models/endpoints outside `models.json` (e.g. your own OpenAI account) unless they are priced
  with a realistic `pricing_key` agreed with a coach.
- The leaderboard shows whatever the app submits — coaches review the top-3 teams' code and strategy before the final
  announcement.

## Allowed and encouraged

Anything in the challenges: prompt changes, retrieval, context minimisation, output limits, caching (exact,
semantic, provider prompt caching), model choice per tier, routing, escalation, retries, gateway policies,
tuning `top_k`, thresholds and `max_output_tokens`, better keyword stopwords, a smarter classifier prompt, etc.
