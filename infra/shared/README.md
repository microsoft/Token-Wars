# Shared-infrastructure mode (teams without an Azure subscription)

> [!WARNING]
> This is an **exception path**. The default Token Wars setup is unchanged: every team deploys `infra/` in its **own**
> subscription. Use this folder only when some teams have no subscription. The coach runs everything here.

## What it builds

```
                       coach subscription
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ rg-tokenwars-<coach>   (infra/, deployed once as usual)                       │
 │   Foundry primary (premium/balanced/economy/embedding/open/judge/custom)      │
 │   Foundry secondary (optional)   Fireworks on Foundry (optional)              │
 │   Ollama Container App (optional)   Log Analytics                             │
 └────────────▲────────────────────────────▲─────────────────────────────────────┘
              │ managed identity            │ managed identity
 ┌────────────┴───────────────┐  ┌──────────┴─────────────────┐
 │ rg-tw-shared-<team-a>-<sfx> │  │ rg-tw-shared-<team-b>-<sfx> │   ... one per line of teams.txt
 │   APIM  /openai  (team key) │  │   APIM  /openai  (team key) │
 │         /judge   (judge key)│  │         /judge   (judge key)│
 │   App Insights (coach LAW)  │  │   App Insights (coach LAW)  │
 └─────────────────────────────┘  └─────────────────────────────┘
```

* **Model deployments are shared.** Each team APIM has the same backends as `infra/apim.tf` (`aoai-primary`,
  `aoai-secondary` + `aoai-pool` if the coach deployed a secondary region, `fireworks`, `selfhosted`), pointing to the
  coach's resources. Its managed identity gets *Cognitive Services OpenAI User* and *Cognitive Services User* on the
  coach Foundry resources (primary, secondary, Fireworks).
* **Azure, Fireworks, self-hosted (Ollama) and custom models** are all served through the team APIM, routed by the
  same policy templates (`infra/policies/*.xml`). A `custom` model (Own the Weights) must be deployed on the coach's
  **primary** Foundry resource and registered with `register_custom_model.py` **before** you run `deploy_teams.py`
  (re-run it afterwards to add the model to every team's `models.json`).
* **The judge also goes through the team APIM**, on a separate coach-managed API (`/judge/v1/`) with its own key
  (`JUDGE_API_KEY`). It only accepts `"model": "judge"`, authenticates with managed identity and is not affected by
  the team's Challenge 3 policy or token limit. **No Foundry or Fireworks key is ever given to a team.**
* **Per team files**: `out/<team>/.env` and `out/<team>/models.json`. No app code changes are needed: the judge
  entry keeps `via_gateway: false`, it just points to `<team-apim>/judge/v1/`.

## Prerequisites (coach)

* The coach stack in `infra/` deployed in the coach subscription with this version of the kit
  (`terraform apply` once more if you deployed earlier: it adds the sensitive `shared_mode_config` output).
  Enable everything teams need **before** creating team gateways: `deploy_fireworks`, `deploy_selfhosted_model`,
  `deploy_secondary_region` (only if you want regional failover in 3.5c), and register `custom` if you demo it.
* Owner (or Contributor + User Access Administrator) on the coach subscription: the script creates resource groups and
  role assignments on the coach Foundry resources.
* Terraform >= 1.6, Python 3.9+ (standard library only), `az login` to the coach tenant.
* Quota: all teams share the coach deployments' TPM. Size `premium/balanced/economy/judge` capacities for
  *teams × concurrency* (see [Limitations](#limitations-and-risks)).

## Deploy the team gateways

```bash
cd infra/shared
cp teams.example.txt teams.txt          # one team name per line
python3 deploy_teams.py                  # plan + confirm; --auto-approve to skip the prompt
```

```powershell
cd infra\shared
Copy-Item teams.example.txt teams.txt
python deploy_teams.py
```

The script validates the list (format, duplicates), reads the coach configuration from `../terraform.tfstate`,
writes `teams.auto.tfvars.json` and runs `terraform apply` in this folder (own local state, git-ignored). It is
idempotent: add names and re-run to add teams. Removing a name would delete that team's resource group, so the script
refuses unless you pass `--allow-remove`.

Give each team **only its own** files:

| File | Goes to | Contains |
| --- | --- | --- |
| `out/<team>/.env` | repo root `.env` | `TOKENWARS_TEAM`, `APIM_SUBSCRIPTION_KEY` (support-squad), `JUDGE_API_KEY`, `APIM_GATEWAY_URL`, `APIM_KEY_<CONSUMER>` for every consumer, leaderboard settings |
| `out/<team>/models.json` | `shared/config/models.json` | coach model registry; every non-judge model `via_gateway: true`; judge on `/judge/v1/` |

Share them over a private channel (one DM per team), never in a common chat. RBAC can take a few minutes to
propagate after the first deployment: `401`/`403` from the backend during that window are expected. Teams then run
`doctor`.

## Challenge 3.5 / 3.6 policies

Teams cannot run `terraform apply`, so the coach applies policies with `set_policy.py`. 3.4 (client-side retry)
needs nothing from the coach.

```bash
python3 set_policy.py --show
python3 set_policy.py --policy ./received/team-a.xml --team team-a                     # a team's edited template
python3 set_policy.py --policy ../policies/ai-gateway-solution.xml --team team-b       # reference solution
python3 set_policy.py --policy ../policies/ai-gateway-multiprovider.xml                # ALL teams (clears overrides)
python3 set_policy.py --reset --team team-a                                            # back to the default
python3 set_policy.py --sync                                                           # re-apply the assignments
```

* Policies are the same **Terraform templates** as in `infra/policies` (keep the `${...}` / `%{ ... }` placeholders).
  Teams edit `infra/policies/ai-gateway-starter.xml` in their clone and send the file to the coach.
* Files outside `infra/policies/` are copied to `team-policies/<team>.xml` (git-ignored).
* With `--team`, only that team's module is applied (`-target`), so a broken template cannot block other teams.
  If the apply fails, the previous assignment is restored.
* The judge API policy (`policies/judge.xml`) is never changed by `set_policy.py`.

## Observability

Every team APIM logs to its own Application Insights (`appi-tws-<team>-<suffix>`) in the coach's Log Analytics
workspace. The `team` metric dimension is the APIM subscription name, which is the same in every team APIM
(`support-squad`, `checkout-squad`), so tell teams apart by resource: open the team's App Insights, or query the
workspace and group by `_ResourceId`. Teams have no portal access: the coach shows dashboards (3.5b) on screen.

## Limitations and risks

* **Shared quota and capacity.** All teams consume the coach deployments' TPM, and the judge deployment is shared too.
  Backend 429s from one team can slow others; the per-consumer `llm-token-limit` (3.5a) only limits each team's own
  gateway. Raise capacities or lower `concurrency` before the event.
* **Shared prompt cache.** Teams that send identical prefixes may benefit from each other's cache hits on the shared
  deployments. Cached-token numbers are less "clean" than in isolated subscriptions.
* **3.5c regional failover** only works if the coach deployed `deploy_secondary_region = true`.
* **3.6 failover demo**: teams cannot lower capacities on shared deployments to provoke 429s (it would affect everybody).
  The coach can run that part as a demo, or apply the multiprovider policy and let teams observe the headers.
* **Cost.** One APIM per team, billed per hour (the coach's `apim_sku`, default StandardV2). Override with
  `-var apim_sku=...` only with a v2 tier that supports the `llm-*` policies. Delete the team gateways right after the event.
* **Fireworks key.** With the coach's `fireworks_backend_auth = "api_key"`, the Fireworks key is stored as a secret named
  value in each team APIM. Teams never see it (they have no Azure access), but rotate it after the event.
* **Ollama** is one shared container: expect higher latency when several teams call it at once.
* **Coach state is local.** `coach_state_path` defaults to `../terraform.tfstate`. Run both stacks from the same machine.

## Cleanup (right after the event)

```bash
python3 deploy_teams.py --destroy        # all team RGs + their role assignments on the coach Foundry resources
rm -rf out team-policies                # generated keys / received policies (PowerShell: Remove-Item -Recurse out, team-policies)
cd .. && terraform destroy              # the coach stack, as usual (destroy the team gateways FIRST)
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `The coach state has no 'shared_mode_config' output` | `cd infra && terraform apply` once (adds the output, no resource changes). |
| `doctor`: judge `403 JudgeOnly` | The team's `models.json` judge entry was edited: `deployment` must be `judge`. Hand out the generated file again. |
| `doctor`: `401` / `PermissionDenied` right after deployment | Role assignment propagation. Wait 5-10 minutes. |
| A model is missing from a team's `models.json` | Enable it in the coach stack (or register `custom`), then re-run `deploy_teams.py`. |
| `These models.json keys are not hosted by a backend ...` | A registered model lives on another resource. Deploy it on the coach's primary Foundry resource. |
| `set_policy.py` fails with a template error | The team's file lost a `${...}` placeholder or `%{ ... }` block. The previous policy is kept; send the error back to the team. |
