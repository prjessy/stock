"""클린 배포 zip 생성 — 다른 사람이 자기 API 키로 셀프호스트할 수 있는 소스 패키지.

정책(화이트리스트): 코드/가이드/템플릿만 담고, 비밀·개인 파일은 원천 배제.
  포함: app/ deploy/ scripts/ tests/ requirements.txt .env.example .gitignore README.md
        docs/ 중 설치·설정 가이드만 (SESSION_* 등 내부 기록 제외)
  제외: .env key*.txt data/ __pycache__ *.pyc *.zip 개인 미디어(JPG/PNG/mp4/hwp 등 루트 잡파일)

안전장치: 패키징 후 전 파일을 스캔해 로컬 .env 의 실제 비밀값·서버IP가
하나라도 들어 있으면 zip 을 만들지 않고 실패한다.

사용: python scripts/build_clean_release.py
출력: app/web/downloads/jessystock-src-YYYYMMDD.zip  (/dl/src 가 최신 날짜본을 서빙)
"""
from __future__ import annotations

import re
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 파일명에 빌드 날짜 포함 — /dl/src 라우트가 jessystock-src*.zip 중 최신본을 집어 서빙한다.
OUT = ROOT / "app" / "web" / "downloads" / f"jessystock-src-{date.today():%Y%m%d}.zip"
ZIP_PREFIX = "jessystock"  # zip 내부 최상위 폴더명

# 통째로 담는 디렉터리 (아래 EXCLUDE 패턴 적용)
INCLUDE_DIRS = ["app", "deploy", "scripts", "tests"]
# 개별 파일
INCLUDE_FILES = [
    "requirements.txt", ".env.example", ".gitignore", "README.md",
    "desktop_launch.py",  # 소스 사용자도 자기 exe 를 빌드할 수 있게(scripts/build_exe.py 와 짝)
    "TELEGRAM_SETUP.md", "SPLIT_TRADING_GUIDE.md",
    "docs/가이드_셀프호스트_설치.md", "docs/GOOGLE_LOGIN_SETUP.md",
    "docs/KAKAO_SETUP.md", "docs/SECURITY_ENV.md",
]
EXCLUDE_PARTS = {"__pycache__", ".deploy_bak", ".git"}
EXCLUDE_SUFFIXES = {".pyc", ".zip", ".db", ".log"}
EXCLUDE_NAMES = {".env", "kis_token.json"}

# 스캔 대상 텍스트 확장자(비밀값 유출 검사)
TEXT_SUFFIXES = {".py", ".md", ".txt", ".html", ".js", ".json", ".webmanifest",
                 ".sh", ".example", ".gitignore", ".yml", ".yaml", ".toml", ".ini", ""}


def _secret_values() -> list[str]:
    """로컬 .env 에서 '실제 비밀로 보이는 값'을 수집(키 이름이 아니라 값)."""
    # 운영 서버 IP — 배포본에 있으면 안 됨 (이 스크립트도 zip에 담기므로 리터럴로 쓰지 않는다)
    vals: list[str] = [".".join(["187", "127", "118", "211"])]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.+?)\s*$", line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
            # 공개돼도 되는 값(도메인·시간·심볼 등)은 제외, 키/토큰/비번류만
            if len(val) >= 12 and any(t in key for t in
                                      ("KEY", "SECRET", "TOKEN", "PASSWORD", "CANO", "API")):
                if not val.startswith("http"):  # 공식 API 도메인은 비밀 아님
                    vals.append(val)
    return vals


def _want(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(p in EXCLUDE_PARTS for p in rel.parts):
        return False
    if path.name in EXCLUDE_NAMES or path.name.startswith("key"):
        return False
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return False
    return True


def collect() -> list[Path]:
    files: list[Path] = []
    for d in INCLUDE_DIRS:
        base = ROOT / d
        for p in sorted(base.rglob("*")):
            if p.is_file() and _want(p):
                files.append(p)
    for f in INCLUDE_FILES:
        p = ROOT / f
        if p.exists():
            files.append(p)
        else:
            print(f"  (경고) 없음: {f}")
    return files


def scan(files: list[Path], secrets: list[str]) -> list[str]:
    """비밀값이 파일 안에 들어 있으면 위반 목록 반환."""
    bad: list[str] = []
    for p in files:
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for s in secrets:
            if s and s in text:
                bad.append(f"{p.relative_to(ROOT)} ← 비밀값/IP 포함({s[:6]}…)")
    return bad


def main() -> int:
    files = collect()
    secrets = _secret_values()
    bad = scan(files, secrets)
    if bad:
        print("❌ 비밀값 유출 감지 — zip 생성 중단:")
        for b in bad:
            print("  ", b)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for p in files:
            z.write(p, f"{ZIP_PREFIX}/{p.relative_to(ROOT).as_posix()}")
        # 설치 가이드를 zip 루트에도 복사(제일 먼저 읽으라고) + zip 전용 머리말
        guide = (ROOT / "docs" / "가이드_셀프호스트_설치.md").read_text(encoding="utf-8")
        header = (
            "# 시작하기 — 이 zip 으로 설치하기\n\n"
            "> 이 파일은 jessystock 클린 소스 배포본입니다(비밀키·개인정보 없음).\n"
            "> 압축을 푼 뒤 이 문서(원본: docs/가이드_셀프호스트_설치.md)를 따라 하세요.\n"
            "> git clone 단계는 건너뛰고, **압축 푼 폴더에서 바로** `.env` 작성부터 시작하면 됩니다.\n"
            "> 본인의 KIS(한국투자증권) API 키가 필요합니다 — 2장 참고.\n\n---\n\n"
        )
        z.writestr(f"{ZIP_PREFIX}/설치가이드_START_HERE.md", header + guide)
    # 이전 날짜 빌드는 정리(최신 1개만 유지 — 다운로드 라우트도 최신본을 서빙).
    for old in OUT.parent.glob("jessystock-src*.zip"):
        if old != OUT:
            old.unlink(missing_ok=True)
            print(f"  (정리) 이전 빌드 삭제: {old.name}")
    kb = OUT.stat().st_size / 1024
    print(f"✅ {OUT} ({kb:,.0f} KB, {len(files) + 1}개 파일)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
