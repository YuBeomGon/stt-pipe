"""문서 ↔ 코드 정합성 가드 (drift 재발 방지).

2026-06-04 정합성 감사에서 DESIGN.md harness 트리와 SSOT §3 코드 책임 지도가
scheduler/portfolio/signature/cooldown/config 5개 모듈을 누락한 게 드러났다
(proposal 은 코드에 흡수됐는데 정본엔 반영 안 됨). 같은 계열의 과거 회귀:
candidate.md 2-mode vs harness 6-mode.

이 테스트는 *진화 엔진 모듈* 이 추가될 때 구조 정본(DESIGN.md, SSOT.md)이 함께
갱신되도록 강제한다 — 새 harness 모듈을 만들고 문서에 안 적으면 실패한다.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_HARNESS = _REPO / "harness"
_DESIGN = _REPO / "docs" / "DESIGN.md"
_SSOT = _REPO / "docs" / "SSOT.md"

# 문서화 의무에서 면제되는 모듈(패키지 보일러플레이트 / 사람이 직접 안 보는 것).
# 새 엔진 모듈은 여기에 넣지 말고 DESIGN/SSOT 에 적어라.
_EXEMPT = {"__init__"}


def _harness_modules() -> set[str]:
    return {
        p.stem
        for p in _HARNESS.glob("*.py")
        if p.stem not in _EXEMPT and not p.stem.startswith("_")
    }


def test_design_md_lists_every_harness_module() -> None:
    text = _DESIGN.read_text(encoding="utf-8")
    missing = sorted(m for m in _harness_modules() if f"{m}.py" not in text)
    assert not missing, (
        f"DESIGN.md harness 트리에 누락된 모듈: {missing}. "
        "엔진 모듈을 추가하면 docs/DESIGN.md 구조도에 함께 적어야 한다."
    )


def test_ssot_code_map_lists_every_harness_module() -> None:
    text = _SSOT.read_text(encoding="utf-8")
    # SSOT §3 는 산문이라 stem 만으로 검사(파일 확장자 없이 언급될 수 있음).
    missing = sorted(m for m in _harness_modules() if m not in text)
    assert not missing, (
        f"SSOT §3 코드 책임 지도에 누락된 모듈: {missing}. "
        "엔진 모듈을 추가하면 docs/SSOT.md §3 에 함께 적어야 한다."
    )
