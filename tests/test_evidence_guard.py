"""证据写盘的共用守卫（`scripts/_evidence.py`）。

`docs/spec-deviations.md` 第 27 行（原 README §10）记着"证据会被脚本自己削薄"这类缺陷，
第 30 行记着它只修了一个脚本。
这里钉三件事：守卫红得了（但不许顺手把真失败也挡住）、守卫绿得起（等量或更厚的重跑照写）、
每个写证据的脚本真的挂在它上面 —— 第三件是防"下一个脚本又自己写一份"的那道闸。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RESULTS = ROOT / "eval" / "results"

sys.path.insert(0, str(SCRIPTS))

import _evidence  # noqa: E402


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def metrics_of(document: dict[str, Any]) -> dict[str, Any]:
    """测试用的尺：条数与"还真量出来了几条"。"""
    premises = document.get("premises", [])
    return {
        "premises": len(premises),
        "measured": sum(1 for item in premises if item.get("ok") is not None),
    }


@pytest.fixture()
def signed(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    document = {"schema": 1, "pass": True, "premises": [{"id": f"p{i}", "ok": True} for i in range(20)]}
    path = tmp_path / "signed.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, document


# ---------------------------------------------------------------- 红得了


def test_a_thinner_evidence_is_refused_and_the_file_is_untouched(signed, capsys) -> None:
    path, document = signed
    before = path.read_bytes()
    thinner = {**document, "premises": document["premises"][:17]}

    assert not _evidence.write_evidence(path, thinner, metrics_of, exact=("premises", "measured"))
    assert path.read_bytes() == before, "说好了拒绝覆盖，却已经把文件改了"
    err = capsys.readouterr().err
    assert "premises：20 → 17" in err
    assert _evidence.FLAG in err, "拒写了，却没说清哪条路还允许写"


def test_a_signed_premise_set_is_not_quietly_unmeasured(signed) -> None:
    """条数一条不少、三条从"量过"变成 null —— 这正是 s14 换错解释器时发生的事。"""
    path, document = signed
    softened = {
        **document,
        "premises": [
            {**item, "ok": None if i < 3 else True} for i, item in enumerate(document["premises"])
        ],
    }
    assert not _evidence.write_evidence(path, softened, metrics_of, exact=("premises", "measured"))


def test_a_clause_that_genuinely_flipped_red_still_gets_written(signed) -> None:
    """守卫只管覆盖，不管结论 —— 判据真翻了红写不进去，证据就没有存在的意义。"""
    path, document = signed
    failed = {
        **document,
        "pass": False,
        "premises": [
            {**item, "ok": False if i < 5 else True} for i, item in enumerate(document["premises"])
        ],
    }
    assert _evidence.write_evidence(path, failed, metrics_of, exact=("premises", "measured"))
    assert json.loads(path.read_text(encoding="utf-8"))["pass"] is False


# ---------------------------------------------------------------- 绿得起


def test_equal_or_greater_coverage_is_written(signed) -> None:
    path, document = signed
    thicker = {**document, "premises": document["premises"] + [{"id": "p20", "ok": True}]}
    assert _evidence.write_evidence(path, thicker, metrics_of, exact=("premises", "measured"))


def test_the_first_signature_is_never_blocked(tmp_path: Path) -> None:
    path = tmp_path / "fresh.json"
    document = {"premises": [{"id": "p0", "ok": True}]}
    assert _evidence.write_evidence(path, document, metrics_of, exact=("premises",))
    assert json.loads(path.read_text(encoding="utf-8")) == document


def test_a_metric_the_old_file_cannot_measure_is_not_held_against_us(signed) -> None:
    """新加的尺上次没这根 —— 拿"上次是 0"判这次变薄，等于让守卫咬自己。"""

    def both(document: dict[str, Any]) -> dict[str, Any]:
        return {**metrics_of(document), "spans": document.get("spans", 0)}

    path, document = signed
    assert _evidence.write_evidence(path, document, both, exact=("spans",))


def test_noise_keeps_its_tolerance_while_coverage_does_not(tmp_path: Path) -> None:
    """九成容差留给"同一批东西重测一遍"的抖动，不给少一份语料。"""
    path = tmp_path / "totals.json"
    path.write_text(json.dumps({"files": 24, "records": 1000}), encoding="utf-8")

    def totals(document: dict[str, Any]) -> dict[str, Any]:
        return {"files": document["files"], "records": document["records"]}

    assert _evidence.write_evidence(path, {"files": 24, "records": 950}, totals, exact=("files",))
    assert not _evidence.write_evidence(path, {"files": 23, "records": 1200}, totals, exact=("files",))


def test_allow_thinning_writes_but_says_what_it_bypassed(signed, capsys) -> None:
    path, document = signed
    thinner = {**document, "premises": document["premises"][:10]}

    assert _evidence.write_evidence(
        path, thinner, metrics_of, exact=("premises", "measured"), allow_thinning=True
    )
    assert "premises：20 → 10" in capsys.readouterr().err, "绕过了守卫，却没留下绕过的痕迹"


def test_evidence_is_written_as_bytes_with_one_trailing_newline(tmp_path: Path) -> None:
    r"""文本模式在 Windows 上把 \n 变成 \r\n —— 同一份 JSON 于是有两个 sha256。"""
    path = tmp_path / "crlf.json"
    assert _evidence.write_evidence(path, {"premises": []}, metrics_of)
    raw = path.read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"\n")


# ------------------------------------------------- 每个脚本都真的挂在守卫上

# 脚本名 → 它签字的那份证据。尺子读不到东西的守卫等于没有守卫。
EVIDENCE_WRITERS = {
    "b2_compact_ab": "b2-compact-ab.json",
    "b3_repomap_ab": "b3-repomap-ab.json",
    "b6_backend_ab": "b6-backend-ab.json",
    "s14_ext_demo": "s14-mcp-skills.json",
    "t3_otlp_export": "t3-otlp.json",
}


@pytest.mark.parametrize("name", sorted(EVIDENCE_WRITERS))
def test_a_script_that_signs_evidence_routes_the_pen_through_the_guard(name: str) -> None:
    source = (SCRIPTS / f"{name}.py").read_text(encoding="utf-8")
    assert "write_evidence(" in source, f"{name} 写证据却绕过了共用守卫"
    for target in ("RESULT", "PROBE_RESULT"):
        for write in (".write_text(", ".write_bytes("):
            assert target + write not in source, f"{name} 还在自己写 {target}{write}"


@pytest.mark.parametrize("name, evidence", sorted(EVIDENCE_WRITERS.items()))
def test_each_ruler_reads_the_committed_evidence_it_protects(
    name: str, evidence: str, tmp_path: Path
) -> None:
    """对着盘上那份真文件量一遍：读数全为 0 的守卫永远不会红。"""
    module = _load(name)
    document = json.loads((RESULTS / evidence).read_text(encoding="utf-8"))
    readings = module._metrics(document)
    assert readings and all(value > 0 for value in readings.values()), f"{name} 的尺子读出了 0：{readings}"
    # 原样重跑不许被挡住：拿真文件在临时目录里自比一次。
    copy = tmp_path / evidence
    shutil.copyfile(RESULTS / evidence, copy)
    assert _evidence.write_evidence(copy, document, module._metrics)


def test_the_probe_ledger_is_measured_in_samples_not_in_words() -> None:
    """账本的覆盖是样本数：它的结论由这几个样本撑着，少一个样本 n 就变了。"""
    module = _load("b2_compact_ab")
    document = json.loads((RESULTS / "b2-live-budget-probe.json").read_text(encoding="utf-8"))
    readings = module._probe_metrics(document)
    assert readings["samples"] == len(document["samples"]) == 2
    assert readings["requests"] == sum(s["requests_sent"] for s in document["samples"]) > 0


def test_the_b3_sentinel_counts_the_clauses_only_a_real_endpoint_can_sign(tmp_path: Path) -> None:
    """live 那 4 条是 B3 因果半条的全部身家：fake 重跑把它们削掉时守卫必须红。"""
    module = _load("b3_repomap_ab")
    document = json.loads((RESULTS / "b3-repomap-ab.json").read_text(encoding="utf-8"))
    assert module._metrics(document)["live_signed"] == len(module._LIVE_CLAUSES)
    fake_only = {
        **document,
        "clauses": [c for c in document["clauses"] if c["id"] not in module._LIVE_CLAUSES],
    }
    assert module._metrics(fake_only)["live_signed"] == 0
    copy = tmp_path / "b3-repomap-ab.json"
    shutil.copyfile(RESULTS / "b3-repomap-ab.json", copy)
    assert not _evidence.write_evidence(copy, fake_only, module._metrics, exact=("clauses", "live_signed"))
    assert copy.read_bytes() == (RESULTS / "b3-repomap-ab.json").read_bytes()
