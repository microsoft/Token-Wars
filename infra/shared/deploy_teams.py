#!/usr/bin/env python3
"""Token Wars - shared-infrastructure mode: create one APIM per team in front of the coach's model resources.

For teams WITHOUT their own Azure subscription. Run it as the coach, after the coach stack in infra/ is deployed.

  python deploy_teams.py                          # reads teams.txt, plans and asks before applying
  python deploy_teams.py --teams my-teams.txt --auto-approve
  python deploy_teams.py --plan                   # plan only
  python deploy_teams.py --allow-remove           # needed when teams were removed from the list (deletes their RGs)
  python deploy_teams.py --destroy                # delete ALL team resource groups (after the event)

teams.txt: one team name per line (2-20 chars: a-z, 0-9, '-'); blank lines and lines starting with '#' are ignored.
Output per team: infra/shared/out/<team>/.env and models.json (secrets - hand them to that team only).
Policies (Challenge 3.5 / 3.6) are managed separately with set_policy.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tw_shared import (HERE, POLICY_TFVARS, TEAM_RE, TEAMS_TFVARS, SharedError, coach_config, current_teams, fail,
                       policy_assignments, prepare_env, print_team_summary, run_tf, tf_init, write_json)


def parse_teams(path: Path, coach_team: str) -> list[str]:
    if not path.exists():
        raise SharedError(f"Team list not found: {path} (copy teams.example.txt to teams.txt and edit it).")
    teams: list[str] = []
    errors: list[str] = []
    for no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        name = raw.strip()
        if not name or name.startswith("#"):
            continue
        if not TEAM_RE.match(name):
            errors.append(f"line {no}: '{name}' (2-20 chars, lowercase a-z, 0-9 and '-', must not start with '-')")
        elif name in teams:
            errors.append(f"line {no}: duplicate team '{name}'")
        else:
            teams.append(name)
    if errors:
        raise SharedError("Invalid team list:\n  " + "\n  ".join(errors))
    if not teams:
        raise SharedError(f"{path} contains no team names.")
    if coach_team in teams:
        print(f"ℹ️  '{coach_team}' is also the coach team name; the team gateway gets its own resource group.")
    return teams


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--teams", default=str(HERE / "teams.txt"), help="team list file (default infra/shared/teams.txt)")
    p.add_argument("--plan", action="store_true", help="only show the plan")
    p.add_argument("--auto-approve", action="store_true", help="apply/destroy without the interactive prompt")
    p.add_argument("--allow-remove", action="store_true",
                   help="allow deleting the gateways of teams that are no longer in the list")
    p.add_argument("--destroy", action="store_true", help="destroy ALL team gateways (the coach stack is untouched)")
    args = p.parse_args(argv)

    try:
        cfg = coach_config()
        prepare_env(cfg)
        tf_init()

        if args.destroy:
            cmd = ["destroy", "-input=false"] + (["-auto-approve"] if args.auto_approve else [])
            if run_tf(cmd).returncode != 0:
                raise SharedError("terraform destroy failed; re-run it (it is idempotent).")
            print("\n✅ All team gateways removed (role assignments on the coach Foundry resources included).")
            print("   Delete infra/shared/out/ to remove the generated keys.")
            return 0

        teams = parse_teams(Path(args.teams), cfg.get("coach_team", ""))
        previous = current_teams()
        removed = [t for t in previous if t not in teams]
        if removed and not args.allow_remove:
            raise SharedError(
                "These teams are deployed but missing from the list: " + ", ".join(removed)
                + ".\nApplying would DELETE their resource groups. Add them back, or re-run with --allow-remove.")

        write_json(TEAMS_TFVARS, {"teams": teams})
        assignments = policy_assignments()
        stale = [t for t in assignments["team_policy_files"] if t not in teams]
        if stale:
            for t in stale:
                assignments["team_policy_files"].pop(t)
            write_json(POLICY_TFVARS, assignments)
            print(f"ℹ️  Dropped policy overrides of removed teams: {', '.join(stale)}")

        added = [t for t in teams if t not in previous]
        print(f"Teams: {len(teams)} (new: {', '.join(added) or 'none'}; removed: {', '.join(removed) or 'none'})")
        print(f"Coach Foundry: {cfg['primary']['subdomain']}  |  Fireworks: {'yes' if cfg.get('fireworks') else 'no'}"
              f"  |  Ollama: {'yes' if cfg.get('selfhosted') else 'no'}  |  secondary region: "
              f"{'yes' if cfg.get('secondary') else 'no'}")

        if args.plan:
            return 0 if run_tf(["plan", "-input=false"]).returncode == 0 else 1
        cmd = ["apply", "-input=false"] + (["-auto-approve"] if args.auto_approve else [])
        if run_tf(cmd).returncode != 0:
            raise SharedError("terraform apply failed. Fix the error and re-run (it is idempotent).")
        print_team_summary()
        print("\n✅ Done. Give each team ONLY its own folder: .env -> repo root, models.json -> shared/config/.")
        print("   New role assignments can take a few minutes to propagate (401/403 until then). "
              "Teams then run `doctor`.")
        return 0
    except SharedError as exc:
        return fail(exc)


if __name__ == "__main__":
    sys.exit(main())
