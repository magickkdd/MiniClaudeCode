# session-store

会话状态：`ttl_minutes` 是**分钟**，`remaining(now)` 还剩多少秒，`humanize()` 把它写成人话。

```python
from store import Session, humanize

s = Session(sid="abc", ttl_minutes=30, created=0.0)
s.expires_at()            # 1800.0 —— 30 分钟后
s.remaining(600)          # 1200.0
s.remaining(5000)         # 0.0，过期之后不返回负数
humanize(3725)            # "1h2m5s"
humanize(125)             # "2m5s"
humanize(45)              # "45s"
humanize(0)               # "0s"
humanize(60)              # "1m0s"
```

`humanize` 的口径：**每一段非零都要出现**，分钟之后的秒即使很小也要写出来。
