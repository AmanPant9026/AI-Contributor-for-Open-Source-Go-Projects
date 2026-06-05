"""Console entry point. Subcommands (run/eval) are wired in later stages."""
from __future__ import annotations
import argparse
from . import __version__


def main() -> None:
    p = argparse.ArgumentParser(prog="agent", description="Agentic Go issue fixer")
    p.add_argument("--version", action="version", version=f"go-issue-agent {__version__}")
    p.add_subparsers(dest="cmd")  # `run`, `eval` added in later stages
    args = p.parse_args()
    if not getattr(args, "cmd", None):
        p.print_help()


if __name__ == "__main__":
    main()
