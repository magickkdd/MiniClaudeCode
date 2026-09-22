"""B2 证据文件的完整性守卫（`scripts/b2_compact_ab.py`）。

live 那 3 条判据只有真端点跑得出来，而脚本默认写同一个文件。这里钉两件事：
不带 `--live` 不许把签过的格子静默削掉；仓库里那份已入库的证据确实带着它们。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_b2_script() -> Any:
    spec = importlib.util.spec_from_file_location(
        "b2_compact_ab", ROOT / "scripts" / "b2_compact_ab.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def b2() -> Any:
    return _load_b2_script()


def test_a_signed_live_evidence_file_is_not_quietly_unsigned(b2: Any, tmp_path: Path) -> None:
    payload = {
        "clauses": [{"id": "off_arm_fails_on_budget", "ok": True}]
        + [{"id": name, "ok": None} for name in b2._LIVE_CLAUSES],
        "live": {"requests_sent": 27},
    }
    evidence = tmp_path / "b2-compact-ab.json"
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    before = evidence.read_bytes()

    b2.RESULT = evidence
    assert b2.unsign_live_guard(5) == 2
    assert evidence.read_bytes() == before, "拒绝覆盖，却已经把文件改了"


def test_evidence_without_a_live_arm_can_still_be_regenerated(b2: Any, tmp_path: Path) -> None:
    """守卫只管"削掉签过的格子"，不管第一次签字 —— 否则 fake 侧再也改不动。"""
    (tmp_path / "b2-compact-ab.json").write_text(
        json.dumps({"clauses": [{"id": "off_arm_fails_on_budget", "ok": True}]}), encoding="utf-8"
    )
    b2.RESULT = tmp_path / "b2-compact-ab.json"
    assert b2.unsign_live_guard(5) is None


def test_the_committed_evidence_carries_the_signed_live_clauses(b2: Any) -> None:
    """README 与 SPEC 里"live 已签"那几段话的生产者。"""
    payload = json.loads((ROOT / "eval" / "results" / "b2-compact-ab.json").read_text(encoding="utf-8"))
    clauses = {c["id"]: c["ok"] for c in payload["clauses"]}
    assert set(b2._LIVE_CLAUSES) <= set(clauses), "已入库的证据里没有 live 判据，文档在说谎"
    assert isinstance(payload["live"], dict) and payload["live"]["requests_sent"] > 0
    assert clauses["live_zero_pairing_400"] is True
    assert clauses["live_ladder_engaged_on_every_run"] is True
    assert clauses["success_rate_unmeasurable"] is None, "未量被悄悄改成了签过"
    assert payload["pass"] is all(
        ok is True for cid, ok in clauses.items() if cid != "success_rate_unmeasurable"
    )
