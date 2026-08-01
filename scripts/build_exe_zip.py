"""윈도우 원클릭 배포 zip 생성 — dist/jessystock.exe 를 사용자용 패키지로 포장.

전제: 먼저 `python scripts/build_exe.py` 로 dist/jessystock.exe 가 있어야 한다.
산출물: app/web/static/jessystock-windows.zip  (사이트 /static/ 으로 서빙)
구성: jessystock.exe · .env(예시에서 복사, 키만 채우면 됨) · 실행방법.txt
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "jessystock.exe"
OUT = ROOT / "app" / "web" / "downloads" / "jessystock-windows.zip"
PREFIX = "jessystock-windows"

GUIDE = """jessystock — 윈도우 실행 방법 (파이썬 설치 불필요)

1) 이 폴더의  .env  파일을 메모장으로 열어
   KIS_APP_KEY / KIS_APP_SECRET 에 본인 한국투자증권 API 키를 넣고 저장하세요.
   (키가 없어도 무료 시세로는 동작합니다. 키 발급: https://apiportal.koreainvestment.com )

2) jessystock.exe 를 더블클릭하세요.
   - 검은 창이 뜨고, 잠시 뒤 브라우저에서 http://127.0.0.1:8000 이 자동으로 열립니다.
   - 종료하려면 그 검은 창을 닫으세요.

3) 처음 실행하면 이 폴더에  data  폴더(설정·기록 저장)가 자동으로 생깁니다.

※ 윈도우 보안경고(“Windows의 PC 보호”)가 뜨면 → 추가 정보 → 실행 을 누르세요
   (서명되지 않은 개인 프로그램이라 뜨는 정상 경고입니다).
※ API 키는 절대 다른 사람과 공유하지 마세요(계좌 위험).
※ 자세한 설정(구글 로그인·텔레그램/카카오 알림·자동매매)은 소스 zip 의 설치가이드를 참고하세요.
"""


def main() -> int:
    if not EXE.exists():
        print(f"❌ {EXE} 없음 — 먼저: python scripts/build_exe.py")
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(EXE, f"{PREFIX}/jessystock.exe")
        z.writestr(f"{PREFIX}/.env", env_example)
        z.writestr(f"{PREFIX}/실행방법.txt", GUIDE)
    mb = OUT.stat().st_size / 1048576
    print(f"✅ {OUT} ({mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
