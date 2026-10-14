"""CLI entry point: python -m tokenwars <run|ask|compare|doctor> [options]."""
from __future__ import annotations

import argparse
import sys

from .config import ConfigError, find_strategy_path, load_config, load_strategy
from . import runner


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--mock", action="store_true", default=argparse.SUPPRESS,
                        help="offline mock mode (no Azure calls); same as TOKENWARS_MOCK=1")
    common.add_argument("--strategy", default=argparse.SUPPRESS, metavar="PATH",
                        help="path to strategy.json (default: ./strategy.json)")

    parser = argparse.ArgumentParser(prog="python -m tokenwars", parents=[common],
                                     description="Token Wars – ByteCart Support Copilot")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", parents=[common], help="answer the workload, judge it and print the scorecard")
    run.add_argument("--limit", type=int, default=None, help="only the first N workload items")
    run.add_argument("--submit", action="store_true", help="submit the summary to the leaderboard")
    run.add_argument("--no-judge", action="store_true", help="skip judging (pass rate unknown)")

    ask = sub.add_parser("ask", parents=[common], help="answer one question with the current strategy")
    ask.add_argument("question")
    ask.add_argument("--customer", default="C1001", help="customer id (default C1001)")

    compare = sub.add_parser("compare", parents=[common], help="compare models on the 30 compare:true items")
    compare.add_argument("--models", default="premium,balanced,economy,open",
                         help="comma-separated model keys (default premium,balanced,economy,open)")
    compare.add_argument("--no-judge", action="store_true", help="skip judging")
    compare.add_argument("--limit", type=int, default=None, help="only the first N compare items")

    sub.add_parser("doctor", parents=[common], help="check configuration, data files and model connectivity")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        config = load_config(mock_flag=getattr(args, "mock", False),
                             strategy_path=find_strategy_path(getattr(args, "strategy", None)))
        strategy = load_strategy(config.strategy_path)
        if args.command == "run":
            return runner.cmd_run(config, strategy, args.limit, not args.no_judge, args.submit)
        if args.command == "ask":
            return runner.cmd_ask(config, strategy, args.question, args.customer)
        if args.command == "compare":
            models = [m.strip() for m in args.models.split(",") if m.strip()]
            return runner.cmd_compare(config, strategy, models, not args.no_judge, args.limit)
        if args.command == "doctor":
            return runner.cmd_doctor(config, strategy)
    except NotImplementedError as exc:
        print(f"\n❌ NotImplementedError: {exc}", file=sys.stderr)
        print("   Implement that TODO, or switch the corresponding flag in strategy.json back off.", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"\n❌ Configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 1


if __name__ == "__main__":
    sys.exit(main())
