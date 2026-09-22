"""执行后端层（SPEC v2 §3.6）：命令在哪儿跑 + 跑完之后怎么退回去了。

依赖约束（§2.3-2）：这一层只允许被 `tools/`、`agent/`、`cli/` 使用，它自己**不 import
`agent/`**，也**不 import `memory/`**。检查点直接落在记忆目录里，路径由装配层给，
所以这层不需要知道记忆层的存在。
"""

from miniclaude.backend.checkpoints import Checkpointer, CheckpointStats, for_workspace
from miniclaude.backend.docker import DockerBackend
from miniclaude.backend.factory import BACKEND_NAMES, BackendChoice, checkpointer_for, select_backend
from miniclaude.backend.local import LocalBackend, merge_env
from miniclaude.backend.protocol import (
    NO_CHECKPOINT_REASON,
    BackendResult,
    Command,
    ExecutionBackend,
)
from miniclaude.backend.sessions import (
    REPLAY_NOTE,
    SESSIONS_DIRNAME,
    SessionLog,
    SessionRecorder,
    SessionSnapshot,
    message_from_dict,
    message_to_dict,
)

__all__ = [
    "BACKEND_NAMES",
    "NO_CHECKPOINT_REASON",
    "REPLAY_NOTE",
    "SESSIONS_DIRNAME",
    "BackendChoice",
    "BackendResult",
    "Checkpointer",
    "CheckpointStats",
    "Command",
    "DockerBackend",
    "ExecutionBackend",
    "LocalBackend",
    "SessionLog",
    "SessionRecorder",
    "SessionSnapshot",
    "checkpointer_for",
    "for_workspace",
    "merge_env",
    "message_from_dict",
    "message_to_dict",
    "select_backend",
]
