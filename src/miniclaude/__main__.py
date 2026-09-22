"""`python -m miniclaude` 的入口，转发到 cli.main:main。

`sys.exit()` 是这条路上唯一交代退出码的地方。少了它，`mcc`（console script 会自己
`sys.exit(main())`）能正确退 1，而 `python -m miniclaude` 永远退 0 —— README §4 说这三个
入口等价，那这句话就得有个东西盯着。
"""

import sys

from miniclaude.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
