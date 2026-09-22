"""让 `import ledger` 在工作目录根下可用（评测把仓库拷进临时目录再跑）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
