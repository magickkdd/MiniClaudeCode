# slug-cli

把标题变成 slug。**清洗规则只应该有一份**：`slug/core.py` 的 `slugify()`。

```python
from slug.core import slugify

slugify("Café Bar!")     # → "caf-bar"
slugify("  --Weird-- ")  # → "weird"
```

命令行把 `--prefix` 与 `--title` 各清洗成一段，用 `--` 连接，空段跳过：

```
$ python -m slug.cli --prefix "Café Bar!" --title "Sale, 2026"
caf-bar--sale-2026
```

`slug/cli.py` 里目前还有一份从 core 复制出去的 `clean()`，两边已经漂了：
带非 ASCII 或结尾标点的前缀，`cli` 洗出来的结果和 `core` 不一样。
它的输出也必须走 `--` 连接的同一套规则。
