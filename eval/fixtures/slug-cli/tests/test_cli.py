from __future__ import annotations

import pytest

from slug.cli import build_parser, main


def test_cli_joins_prefix_and_title():
    assert main(["--prefix", "ops", "--title", "Sale, 2026"]) == "ops--sale-2026"


def test_cli_skips_empty_prefix():
    assert main(["--title", "Hello World"]) == "hello-world"


def test_title_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--prefix", "ops"])


def test_prefix_and_title_share_one_cleaning_rule():
    """前缀和标题必须被洗成同一个样子 —— 现在不是。"""
    assert main(["--prefix", "Café Bar!", "--title", "Café Bar!"]) == "caf-bar--caf-bar"
