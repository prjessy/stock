"""dedupe 단위 테스트 (임시 SQLite 파일 사용).

검증:
  - 최초엔 미발송 → 기록 후엔 발송됨으로 인식
  - 같은 (거래일, 종목, 임계값) 재기록은 False(중복)
  - 다른 임계값은 독립
  - 다른 거래일은 자동 재무장(R-19/AC-5)
"""
import os
import tempfile

from app.core import dedupe
from app.storage.db import init_db


def _fresh_repo():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return init_db(path), path


def test_first_not_sent_then_marked():
    repo, path = _fresh_repo()
    try:
        assert dedupe.already_sent(repo, "2026-06-19", "005930", 3.0) is False
        assert dedupe.mark_sent(repo, "2026-06-19", "005930", 3.0) is True
        assert dedupe.already_sent(repo, "2026-06-19", "005930", 3.0) is True
    finally:
        repo.close()
        os.remove(path)


def test_duplicate_mark_returns_false():
    repo, path = _fresh_repo()
    try:
        assert dedupe.mark_sent(repo, "2026-06-19", "005930", 3.0) is True
        assert dedupe.mark_sent(repo, "2026-06-19", "005930", 3.0) is False
    finally:
        repo.close()
        os.remove(path)


def test_different_threshold_independent():
    repo, path = _fresh_repo()
    try:
        assert dedupe.mark_sent(repo, "2026-06-19", "005930", 3.0) is True
        # -3.0 은 별개 키 → 아직 미발송.
        assert dedupe.already_sent(repo, "2026-06-19", "005930", -3.0) is False
        assert dedupe.mark_sent(repo, "2026-06-19", "005930", -3.0) is True
    finally:
        repo.close()
        os.remove(path)


def test_new_trade_date_rearms():
    repo, path = _fresh_repo()
    try:
        assert dedupe.mark_sent(repo, "2026-06-19", "005930", 3.0) is True
        # 다음 거래일은 키가 달라 자동 재무장.
        assert dedupe.already_sent(repo, "2026-06-20", "005930", 3.0) is False
        assert dedupe.mark_sent(repo, "2026-06-20", "005930", 3.0) is True
    finally:
        repo.close()
        os.remove(path)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("OK", name)
    print("dedupe: 전체 통과")
