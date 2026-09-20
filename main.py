#!/usr/bin/env python3
"""零安装入口：在项目目录里 `python main.py` 就能跑起来。

`pip install -e .` 之后请改用 `mcc` 或 `python -m miniclaude`；
这个文件只为"clone 下来立刻能试"这一条体验服务。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from miniclaude.cli.main import main  # noqa: E402 - 必须先补 sys.path 才能 import

if __name__ == "__main__":
    raise SystemExit(main())
