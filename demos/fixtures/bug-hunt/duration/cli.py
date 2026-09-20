"""`python -m duration` 的入口。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .format import format_duration
from .parse import parse_duration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="duration", description="解析或格式化时长。")
    parser.add_argument("seconds", nargs="?", type=int, default=None, help="要格式化的秒数")
    parser.add_argument("--text", default=None, help="要解析的字符串，例如 1h30m")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.text is not None:
        print(format_duration(parse_duration(args.text)))
        return 0
    if args.seconds is None:
        build_parser().print_help()
        return 2
    print(format_duration(args.seconds))
    return 0
