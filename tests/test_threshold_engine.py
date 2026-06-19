"""threshold_engine 단위 테스트 (순수 함수, I/O 없음).

경계값 검증: 정확히 +3.0 은 교차, +2.9 는 미교차. 다단계 임계값 지원.
plain assert 로 작성해 `python -m pytest` 또는 직접 실행 모두 가능.
"""
from app.core.threshold_engine import crossed_thresholds, is_crossed


def test_positive_boundary_exact_crosses():
    assert is_crossed(3.0, 3.0) is True
    assert crossed_thresholds(3.0, [3.0, -3.0]) == [3.0]


def test_positive_just_below_does_not_cross():
    assert is_crossed(2.9, 3.0) is False
    assert crossed_thresholds(2.9, [3.0, -3.0]) == []


def test_negative_boundary_exact_crosses():
    assert is_crossed(-3.0, -3.0) is True
    assert crossed_thresholds(-3.0, [3.0, -3.0]) == [-3.0]


def test_negative_just_above_does_not_cross():
    assert is_crossed(-2.9, -3.0) is False
    assert crossed_thresholds(-2.9, [3.0, -3.0]) == []


def test_multi_level_returns_all_crossed_preserving_order():
    # +7% 는 +3, +6 둘 다 교차하고 음수 임계값은 미교차.
    assert crossed_thresholds(7.0, [3.0, -3.0, 6.0, -6.0]) == [3.0, 6.0]


def test_multi_level_negative():
    assert crossed_thresholds(-6.5, [3.0, -3.0, 6.0, -6.0]) == [-3.0, -6.0]


def test_none_change_pct_returns_empty():
    # 시세 조회 실패(change_pct=None) 는 교차 없음으로 처리.
    assert crossed_thresholds(None, [3.0, -3.0]) == []


def test_zero_threshold_uses_positive_rule():
    assert is_crossed(0.0, 0.0) is True
    assert is_crossed(-0.1, 0.0) is False


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("OK", name)
    print("threshold_engine: 전체 통과")
