"""打印每个考题 fixture 的基线用例状态 —— 出白名单时对着它抄，不靠记忆。

用法：

```
python scripts/s9_fixture_state.py                 # 全部 fixture
python scripts/s9_fixture_state.py taxed-base      # 只看一个
```

输出的是 `contract.node_ids()` 的结果，也就是判据实际会看到的用例 id：
`fail_to_pass` 只能从「红」里挑（或明确是要模型新建的），`pass_to_pass` 只能从「绿」里挑。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for entry in (ROOT / "src",):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from miniclaude.eval.contract import node_ids  # noqa: E402

FIXTURE_DIRS = [ROOT / "demos" / "fixtures", ROOT / "eval" / "fixtures"]


def main(argv: list[str]) -> int:
    wanted = set(argv)
    found = 0
    for base in FIXTURE_DIRS:
        if not base.is_dir():
            continue
        for fixture in sorted(p for p in base.iterdir() if p.is_dir()):
            if wanted and fixture.name not in wanted:
                continue
            found += 1
            green, red = node_ids(fixture)
            print(f"\n=== {fixture.relative_to(ROOT).as_posix()} · {len(green)} 绿 / {len(red)} 红 ===")
            for node in green:
                print(f"  [green] {node}")
            for node in red:
                print(f"  [RED]   {node}")
            if not green and not red:
                print("  （没有用例）")
    if not found:
        print(f"没找到 fixture：{', '.join(sorted(wanted))}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
