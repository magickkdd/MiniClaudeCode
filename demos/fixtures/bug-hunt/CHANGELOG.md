# 变更日志

## 0.4.2
- CLI 增加 `--text`，直接解析 `1h30m` 这类写法。

## 0.4.0
- 新增 `Stopwatch`，`label()` 复用 `format_duration`。

## 0.3.0
- `parse_duration` 允许乱序单位（`30m1h`）。
- 负数输入统一抛 `ValueError`。

## 0.2.0
- `parse_duration` 支持 `d` 单位。
- README 补上格式化对照表。
