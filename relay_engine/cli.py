"""python -m relay_engine pull <feed> | create-demo

Only the command line exists so far: each command is wired in as its
client or the creator is built.
"""

import argparse
import sys

from relay_engine.feeds import FeedName


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    print(f"{arguments.command} isn't built yet", file=sys.stderr)
    return 1


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m relay_engine")
    commands = parser.add_subparsers(dest="command", required=True)

    pull = commands.add_parser("pull", help="fetch one feed from its source and deliver it to bi-engine")
    pull.add_argument("feed", choices=list(FeedName))

    commands.add_parser("create-demo", help="AI drafts a record, then creates it via API")
    return parser.parse_args(argv)
