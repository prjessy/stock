"""jessystock 단일 exe 빌드 (PyInstaller, Windows).

산출물: dist/jessystock.exe  (더블클릭 실행 — 파이썬 설치 불필요)
사용:  python scripts/build_exe.py
주의:  이 스크립트를 실행한 OS 용 exe 만 나온다(Windows 에서 빌드=윈도우용).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEP = ";" if sys.platform.startswith("win") else ":"


def data(src_rel: str, dst_rel: str) -> str:
    return f"--add-data={ROOT / src_rel}{SEP}{dst_rel}"


def main() -> int:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", "jessystock",
        # 번들 데이터 — 코드가 Path(__file__) 로 찾는 파일들을 같은 상대경로에 배치
        data("app/web/static", "app/web/static"),
        data("app/datasources/krx_names.json", "app/datasources"),
        data("app/datasources/us_names.json", "app/datasources"),
        # 동적 import 되는 패키지들(하드코딩 import 가 없어 PyInstaller 가 놓치기 쉬움)
        "--collect-all", "FinanceDataReader",
        "--collect-all", "yfinance",
        "--collect-submodules", "uvicorn",
        "--collect-submodules", "apscheduler",
        "--hidden-import", "app.web.api",
        str(ROOT / "desktop_launch.py"),
    ]
    print("빌드 시작 (수 분 소요)…")
    return subprocess.call(args, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
