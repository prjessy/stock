"""대시보드 실행 엔트리.

    python -m app.web

로 uvicorn 을 띄운다. host 0.0.0.0 으로 두어 VPS 에서도 접속 가능.
로컬에서는 http://localhost:8000 으로 접속.
"""
from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("app.web.api:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
