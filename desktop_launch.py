"""jessystock 데스크톱 런처 — PyInstaller 로 묶어 '더블클릭 실행' exe 를 만든다.

- .env / data(stock.db·설정·토큰) 는 **실행파일이 있는 폴더** 기준으로 읽고 쓴다.
  (사용자가 exe 옆에 .env 만 두면 그 키로 동작 — 파이썬/설치 불필요)
- 서버가 뜨면 기본 브라우저를 자동으로 연다(http://127.0.0.1:8000).
- 콘솔 창을 닫으면 종료된다.

빌드: scripts/build_exe.py  (또는 아래 build 명령 참고)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _app_dir() -> Path:
    """exe 실행 시=실행파일 폴더, 소스 실행 시=이 파일 폴더."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


# .env·data 를 exe 옆에서 찾도록 cwd 를 먼저 고정한 뒤에 앱을 import 한다
# (app.config 가 import 시점에 load_dotenv() 로 현재 폴더의 .env 를 읽기 때문).
os.chdir(_app_dir())

import socket
import threading
import time
import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8000


def _open_browser_when_ready() -> None:
    for _ in range(120):  # 최대 ~60초 대기
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.5)
    try:
        webbrowser.open(f"http://{HOST}:{PORT}")
    except Exception:
        pass


def main() -> None:
    if not (_app_dir() / ".env").exists():
        print("!! .env 파일이 없습니다 — .env.example 을 복사해 KIS 키를 넣으세요.")
        print("   (키 없이도 무료 시세로는 동작하지만 실시간·자동매매는 제한됩니다)\n")
    print("=" * 54)
    print("  jessystock — 주식 레이더 (로컬 실행)")
    print(f"  브라우저에서 http://{HOST}:{PORT} 가 자동으로 열립니다.")
    print("  종료하려면 이 창을 닫으세요.")
    print("=" * 54)
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    # reload=False, 단일 프로세스 — 묶인 exe 안에서 하위프로세스 없이 동작.
    uvicorn.run("app.web.api:app", host=HOST, port=PORT, reload=False, log_level="warning")


if __name__ == "__main__":
    main()
