"""命令行入口。

历史上从这里复制过一份清洗实现 —— 复制的那份漏了去首尾 `-`，
所以带标点或重音的前缀会洗出和 `core.slugify` 不一样的结果。
"""

from __future__ import annotations

import argparse
import re

from .core import slugify

_PREFIX_NOISE = re.compile(r"[^a-z0-9]+")


def clean(text: str) -> str:
    """`core.slugify` 的复制版，少了收尾的 `strip("-")`。"""
    return _PREFIX_NOISE.sub("-", (text or "").lower())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="slug", description="把标题变成 slug。")
    parser.add_argument("--prefix", default="", help="可选前缀，与标题之间用 -- 连接")
    parser.add_argument("--title", required=True, help="要清洗的标题")
    return parser


def main(argv: list[str] | None = None) -> str:
    args = build_parser().parse_args(argv)
    parts = [segment for segment in (clean(args.prefix), slugify(args.title)) if segment]
    return "--".join(parts)


if __name__ == "__main__":  # pragma: no cover - 手动跑的时候用
    print(main())
