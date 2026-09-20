# duration —— 面向人的时长解析与格式化

只依赖标准库。给 CLI、报表和日志脱敏脚本用。

## 命令行

```
$ python -m duration --text 1h30m
1h30m0s
$ python -m duration 90061
1d1h1m1s
```

## API

### `parse_duration(text) -> int`

把 `1d2h3m4s` 解析成秒数（93784）。单位只认 `d/h/m/s`，顺序无所谓，多余字符报 `ValueError`。

### `format_duration(seconds) -> str`

向下取整，从最高的非零单位开始打印，每段补零到个位：

| 输入 | 输出 |
|---:|---|
| 0 | `0s` |
| 45 | `45s` |
| 119 | `1m59s` |
| 3661 | `1h1m1s` |
| 90000 | `1d1h0m0s` |
| 90061 | `1d1h1m1s` |

超过一天时保留 `d` 段，后面的 `h/m/s` 照旧打印（哪怕是 0）。

## 约定

- 负数秒一律 `ValueError`，不返回 `-1h` 这种串。
- `Stopwatch` 只做秒表，不做线程安全保证。
