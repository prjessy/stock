"""alerts 서비스 테스트 — 합성 quote 로 임계값 교차 시연.

실제 시세가 ±3% 를 넘지 않을 수 있으므로, 가짜 registry/notifier 로
교차 → 메시지 구성 → 발송 → 중복방지 흐름을 결정적으로 검증한다.
프로덕션 코드에는 어떤 테스트용 분기도 넣지 않는다(여기서만 주입).
"""
import os
import tempfile

from app.core import alerts
from app.notify.base import Notifier
from app.storage.db import init_db


class FakeRegistry:
    """all_quotes() 만 흉내내는 가짜 SourceRegistry."""

    def __init__(self, quotes):
        self._quotes = quotes

    def all_quotes(self):
        return self._quotes


class CapturingNotifier(Notifier):
    """전송 메시지를 모아두는 가짜 Notifier."""

    def __init__(self, succeed=True):
        self.messages = []
        self.succeed = succeed

    def send_message(self, text):
        self.messages.append(text)
        return self.succeed

    def send_report(self, payload):
        return self.succeed


def _quote(symbol, name, price, prev_close, change_pct, currency="KRW"):
    return {
        "symbol": symbol, "name": name, "price": price, "prev_close": prev_close,
        "change_pct": change_pct, "currency": currency, "note": "", "ts": 0, "error": "",
    }


def _fresh_repo():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return init_db(path), path


def test_synthetic_crossing_fires_with_context():
    repo, path = _fresh_repo()
    try:
        quotes = [
            _quote("005930", "삼성전자", 88000, 80000, 10.0),   # +10% → +3 교차
            _quote("NQ=F", "나스닥 선물", 20120, 20000, 0.6, "USD"),
        ]
        notifier = CapturingNotifier()
        registry = FakeRegistry(quotes)

        sent = alerts.run_once(repo, notifier, registry)

        assert sent == 1
        msg = notifier.messages[0]
        assert "삼성전자" in msg and "005930" in msg
        assert "10.0%" in msg
        assert "나스닥 선물 +0.6%" in msg          # 맥락(R-18)
        assert "프리장 강세" in msg
        assert "※ 데이터 약 15분 지연" in msg        # 푸터(R-17)
        assert "현재가" in msg and "전일" in msg
        print("\n[합성 교차 메시지]\n" + msg)

        # 같은 패스 재실행은 중복 방지로 0건.
        assert alerts.run_once(repo, notifier, registry) == 0
    finally:
        repo.close()
        os.remove(path)


def test_send_failure_does_not_mark_sent():
    repo, path = _fresh_repo()
    try:
        quotes = [_quote("000660", "SK하이닉스", 200000, 220000, -9.1)]
        notifier = CapturingNotifier(succeed=False)  # 전송 실패
        registry = FakeRegistry(quotes)

        assert alerts.run_once(repo, notifier, registry) == 0
        # 실패했으니 mark_sent 안 됨 → 다음 성공 패스에서 발송 가능.
        notifier.succeed = True
        assert alerts.run_once(repo, notifier, registry) == 1
    finally:
        repo.close()
        os.remove(path)


def test_nq_self_reference_skipped():
    repo, path = _fresh_repo()
    try:
        quotes = [_quote("NQ=F", "나스닥 선물", 21000, 20000, 5.0, "USD")]
        notifier = CapturingNotifier()
        alerts.run_once(repo, notifier, FakeRegistry(quotes))
        msg = notifier.messages[0]
        # NQ 자기 알림엔 나스닥 선물 맥락 줄을 중복 표기하지 않음.
        assert "프리장" not in msg
        assert "※ 데이터 약 15분 지연" in msg
    finally:
        repo.close()
        os.remove(path)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("OK", name)
    print("alerts: 전체 통과")
