"""임계값 판정 엔진 (순수 함수, I/O 없음).

전일 종가 대비 등락률(change_pct)이 설정된 임계값을 "교차"했는지 판정한다.
다단계(예: [+3,-3,+6,-6])를 지원하며, 각 임계값은 독립적으로 평가한다.

교차 규칙:
  - 양(+) 임계값 T: change_pct >= T 이면 교차.
  - 음(-) 임계값 T: change_pct <= T 이면 교차.

이 모듈은 저장/네트워크/시각 의존이 전혀 없어 단위 테스트가 쉽다.
중복 발송 방지(거래일 1회)는 dedupe 계층의 책임이다.
"""
from __future__ import annotations


def is_crossed(change_pct: float, threshold: float) -> bool:
    """등락률이 단일 임계값을 교차했는지 판정한다.

    양수 임계값은 '이상', 음수 임계값은 '이하'를 교차로 본다.
    0 임계값은 양수 규칙(>=)으로 처리한다.
    """
    if threshold >= 0:
        return change_pct >= threshold
    return change_pct <= threshold


def crossed_thresholds(change_pct: float | None, thresholds: list[float]) -> list[float]:
    """현재 교차 상태인 임계값들을 반환한다.

    Args:
        change_pct: 전일 종가 대비 등락률(%). None 이면(시세 실패) 빈 리스트.
        thresholds: 설정된 임계값 리스트(예: [3.0, -3.0, 6.0, -6.0]).

    Returns:
        교차된 임계값 리스트. 입력 순서를 보존한다.
    """
    if change_pct is None:
        return []
    return [t for t in thresholds if is_crossed(change_pct, t)]
