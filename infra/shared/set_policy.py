#!/usr/bin/env python3
"""Token Wars - shared-infrastructure mode: apply a Challenge 3.5 / 3.6 policy to the team APIMs (coach only).

  python set_policy.py --show                                                       # current assignments
  python set_policy.py --policy ../policies/ai-gateway-solution.xml                 # ALL teams (clears overrides)
  python set_policy.py --policy ../policies/ai-gateway-multiprovider.xml --team team-a --team team-b
  python set_policy.py --policy ./received/team-a.xml --team team-a                 # a team's own edited template
  python set_policy.py --reset --team team-a                                        # back to the default policy
  python set_policy.py --sync                                                       # re-apply current assignments

Policies are Terraform templates (same variables as infra/apim.tf). A file outside infra/ is copied to
infra/shared/team-policies/ so the assignment survives the original file moving. If the apply fails, the previous
assignment is restored in policies.auto.tfvars.json. The judge API policy is never touched.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from tw_shared import (HERE, INFRA, POLICY_TFVARS, SharedError, coach_config, current_teams, fail,
                       policy_assignments, prepare_env, print_team_summary, read_json, run_tf, tf_init, write_json)

TEAM_POLICY_DIR = HERE / "team-policies"


def store_policy(src: Path, label: str) -> str:
    """Return the path stored in tfvars: relative to infra/ for the kit's policies/, else a copy in team-policies/."""
    src = src.resolve()
    if not src.is_file():
        raise SharedError(f"Policy file not found: {src}")
    if "<policies" not in src.read_text(encoding="utf-8-sig"):
        raise SharedError(f"{src} does not look like an APIM policy (no <policies> element).")
    if src.parent == (INFRA / "policies").resolve():
        return src.relative_to(INFRA.resolve()).as_posix()
    TEAM_POLICY_DIR.mkdir(exist_ok=True)
    dest = TEAM_POLICY_DIR / f"{label}.xml"
    if src != dest.resolve():
        shutil.copyfile(src, dest)
    return dest.relative_to(INFRA).as_posix()


def show(assignments: dict, teams: list[str]) -> None:
    print(f"Default policy : {assignments['default_policy_file']}")
    if not teams:
        print("No teams deployed yet (run deploy_teams.py).")
    for t in teams:
        override = assignments["team_policy_files"].get(t)
        print(f"  - {t:<20} {override or assignments['default_policy_file']}{'  (override)' if override else ''}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--policy", help="policy template to apply")
    p.add_argument("--team", action="append", default=[], help="limit to this team (repeatable); default: all teams")
    p.add_argument("--reset", action="store_true", help="remove the override of --team(s) (use the default policy)")
    p.add_argument("--sync", action="store_true", help="re-apply the current assignments to all teams")
    p.add_argument("--show", action="store_true", help="print the current assignments and exit")
    p.add_argument("--plan", action="store_true", help="only show the plan (assignment file is restored)")
    p.add_argument("--auto-approve", action="store_true", help="apply without the interactive prompt")
    args = p.parse_args(argv)

    teams = current_teams()
    before = read_json(POLICY_TFVARS, None)
    assignments = policy_assignments()
    try:
        if args.show:
            show(assignments, teams)
            return 0
        if sum(bool(x) for x in (args.policy, args.reset, args.sync)) != 1:
            raise SharedError("Use exactly one of --policy, --reset or --sync (or --show).")
        if not teams:
            raise SharedError("No teams deployed yet: run deploy_teams.py first.")
        unknown = [t for t in args.team if t not in teams]
        if unknown:
            raise SharedError(f"Unknown team(s): {', '.join(unknown)}. Deployed: {', '.join(teams)}")
        if args.reset and not args.team:
            raise SharedError("--reset needs --team (to reset everyone, use --policy with the default policy).")

        if args.policy and args.team:
            for t in args.team:
                assignments["team_policy_files"][t] = store_policy(Path(args.policy), t)
        elif args.policy:
            assignments["default_policy_file"] = store_policy(Path(args.policy), "_default")
            cleared = sorted(assignments["team_policy_files"])
            assignments["team_policy_files"] = {}
            if cleared:
                print(f"ℹ️  Cleared per-team overrides: {', '.join(cleared)} (every team gets the new policy)")
        elif args.reset:
            for t in args.team:
                assignments["team_policy_files"].pop(t, None)
        write_json(POLICY_TFVARS, assignments)
        show(assignments, teams)

        cfg = coach_config()
        prepare_env(cfg)
        tf_init()
        # A team-scoped change only touches that team's module, so a broken template of one team cannot block others.
        targets = [f'-target=module.team["{t}"]' for t in args.team]
        verb = "plan" if args.plan else "apply"
        cmd = [verb, "-input=false", *targets] + (["-auto-approve"] if args.auto_approve and not args.plan else [])
        ok = run_tf(cmd).returncode == 0
        if args.plan or not ok:
            restore(before)
        if not ok:
            raise SharedError("terraform failed; the previous policy assignment was restored in "
                              f"{POLICY_TFVARS.name}. Teams that were already updated keep the new policy until "
                              "the next set_policy.py run (use --sync to re-converge).")
        if not args.plan:
            print_team_summary()
            print("\n✅ Policy applied. Teams re-run their app; nothing changes on their side (same keys and URLs).")
        return 0
    except SharedError as exc:
        return fail(exc)


def restore(before) -> None:
    if before is None:
        POLICY_TFVARS.unlink(missing_ok=True)
    else:
        write_json(POLICY_TFVARS, before)


if __name__ == "__main__":
    sys.exit(main())
