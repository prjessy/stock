# 윈도우 터미널 한글 깨짐 · 비프음 해결 가이드

> Windows PowerShell에서 **한글이 깨지거나(��)**, **삑 소리(벨)가 나거나**, **git에서 한글 파일명이 `\354\225\204…`로 보이는** 문제를 한 번에 잡는 가이드.
> 설정은 **PC마다 따로** 적용해야 한다(한 PC에서 했다고 다른 PC에 자동 적용 안 됨). 새 PC에서는 아래 **① 원샷 스크립트**만 붙여넣으면 끝.

---

## 왜 이런 일이 생기나 (원인)

| 증상 | 원인 |
|---|---|
| 한글이 `??` / `��` 로 깨짐 | 콘솔 코드페이지가 UTF-8(65001)이 아님 + PowerShell 기본 인코딩이 UTF-8 아님 |
| git status에서 한글 파일명이 `\354\225\204…` | git `core.quotepath` 기본값이 `true`(비ASCII를 8진수로 이스케이프) |
| 한글 입력·줄편집 때 **삑 소리** | 버그 아님 — 터미널 **벨(bell)**. PSReadLine이 편집 경고음을 냄 |
| 파일 저장했더니 다른 도구가 못 읽음 | PowerShell 5.1이 파일을 UTF-16(BOM)으로 저장 → `-Encoding utf8` 필요 |

---

## ① 원샷 스크립트 (새 PC에서 이것만 붙여넣기)

**Windows PowerShell**(검은 창)을 열고 **아래 전체를 복사해 붙여넣고 Enter**:

```powershell
# 0) ★실행정책 풀기 — 이게 없으면 프로필(.ps1)이 실행 차단돼 아무 설정도 안 먹는다(핵심!)
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force

# 1) git 한글 파일명 그대로 보이게
git config --global core.quotepath false

# 2) PowerShell 프로필에 UTF-8 + 벨끄기 영구 적용
$dir = Split-Path $PROFILE
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
@'
# === UTF-8 한글 깨짐 방지 ===
try { chcp 65001 > $null } catch {}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
# 터미널 벨(삑 소리) 끄기 — 한글 입력/줄편집 시 비프음 방지
try { Set-PSReadLineOption -BellStyle None } catch {}
# ============================
'@ | Set-Content -Path $PROFILE -Encoding utf8

# 3) 지금 창에 즉시 반영
. $PROFILE
Write-Host ("완료! Enc=" + [Console]::OutputEncoding.EncodingName + " / Bell=" + (Get-PSReadLineOption).BellStyle)
```

> 마지막 줄에 `완료! Enc=Unicode (UTF-8) / Bell=None` 이 나오면 성공. 이 스크립트는 현재 창에도 즉시 적용되므로 창을 새로 안 열어도 된다.
>
> ⚠️ **0번(실행정책)이 진짜 핵심.** Windows PowerShell 기본값은 `Restricted`라 프로필 스크립트 실행이 막혀 있다. 이걸 안 풀면 프로필을 만들어도 창을 열 때 무시돼서 "여전히 안 됨" 증상이 난다(다른 PC에서 안 됐던 원인).

---

## ② 시스템 전역 UTF-8 (가장 확실 · 1회 · 재부팅 필요)

위 ①로도 대부분 해결되지만, 그래도 깨지는 프로그램이 있으면 시스템 차원에서 UTF-8을 켠다.

1. **설정 → 시간 및 언어 → 언어 및 지역 → 관리 언어 설정** (검색창에 "국가 또는 지역" 검색해도 됨)
2. **시스템 로캘 변경…** 클릭
3. **"Beta: 세계 언어 지원을 위해 Unicode UTF-8 사용"** 체크
4. **재부팅**

> ⚠️ 일부 옛 한글 전용 프로그램이 글자 깨질 수 있음. 그런 경우만 체크 해제.

---

## ③ Windows Terminal 쓸 때 (권장)

레거시 콘솔(conhost)보다 **Windows Terminal**이 한글·이모지·UTF-8에 훨씬 안정적이다.

- 설치: Microsoft Store에서 "Windows Terminal" (Win11은 기본 내장)
- **벨 끄기**: 설정(Ctrl+,) → 해당 프로필 → **고급 → 벨 알림 스타일 → "없음"**
  (또는 `settings.json`에 `"bellStyle": "none"`)
- **글꼴**: 한글 글리프 있는 고정폭 폰트 권장 — **D2Coding**, **나눔고딕코딩**, 또는 **Cascadia Mono**

---

## ④ 확인 방법

새 PowerShell 창에서:

```powershell
# 인코딩이 UTF-8인지
[Console]::OutputEncoding.EncodingName        # → Unicode (UTF-8)
chcp                                          # → 65001

# 벨이 꺼졌는지
(Get-PSReadLineOption).BellStyle              # → None

# git 한글 파일명
git config --global core.quotepath            # → false

# 한글 출력 테스트
Write-Host "한글 테스트 — 가나다 ★ 삼성전자 955,416원"
```

---

## ⑤ 자주 겪는 추가 함정

- **파일을 PowerShell로 만들 때 깨짐**: `Out-File`/`Set-Content`에 `-Encoding utf8`을 꼭 붙인다(기본은 UTF-16 BOM).
- **`> 파일` 리다이렉트도 UTF-16이 됨**: `명령 | Out-File -Encoding utf8 파일` 로.
- **VS Code 터미널도 깨짐**: VS Code 설정에서 `"terminal.integrated.defaultProfile.windows": "PowerShell"` + 위 프로필이 함께 동작. 파일 인코딩은 우하단 "UTF-8" 확인.
- **SSH로 VPS 접속 시 한글 로그 깨짐**: 위 ① 적용 + 접속 후 VPS에서 `locale` 이 `UTF-8`인지 확인(`LANG=ko_KR.UTF-8` 또는 `C.UTF-8`).

---

## ⑥ Claude Code(TUI) 화면이 깨질 때 — Windows Terminal + D2Coding 폰트

**증상:** 파란 "Windows PowerShell"(레거시 콘솔)에서 `claude` 를 실행하면 화면 가장자리에
글자가 줄줄이 새거나 박스/테두리가 어긋난다.

**원인(버그 아님 · 알려진 한계):** 레거시 콘솔(conhost)은 Claude Code 같은 TUI(화면을
실시간으로 다시 그리는 프로그램)의 ANSI 재출력·한글 2칸폭을 제대로 처리 못 한다.
마이크로소프트도 레거시 콘솔 대신 **Windows Terminal** 사용을 권장한다.
→ **Windows Terminal로 옮기되, 한글이 깨지지 않게 한글 코딩폰트(D2Coding)를 지정**하면 끝.

### STEP 1 — D2Coding 폰트 설치
1. `github.com/naver/d2codingfont` → **Releases** → `D2Coding-Ver….zip` 다운로드
2. 압축 풀고 `.ttf` 파일 우클릭 → **"모든 사용자용으로 설치"** (ligature 버전·일반 버전 둘 다 설치 가능)

### STEP 2 — Windows Terminal 글꼴 변경
1. **Windows Terminal** 열기(`Win`→`terminal`). 없으면 Microsoft Store에서 설치(Win11은 기본 내장)
2. 탭 줄 **아래 화살표(˅) → 설정** (또는 `Ctrl + ,`)
3. 왼쪽 **"Windows PowerShell"** 프로필 → **모양(Appearance) → 글꼴(Font face) → `D2Coding`** → **저장**

### STEP 3 — UTF-8/벨끄기 프로필 적용 (그 PC에 아직 안 했으면)
위 **① 원샷 스크립트** 또는 `notepad $PROFILE` 로 4줄(chcp/OutputEncoding/$OutputEncoding/BellStyle) +
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force`.

### STEP 4 — 확인
Windows Terminal **새 탭**에서 `claude` 실행 → 화면 안 깨지고 한글 또렷하면 성공.

> 정리: **레거시 파란 콘솔 = TUI 깨짐(한계)**, **Windows Terminal + D2Coding = 화면·한글 둘 다 정상.**
> VS Code 통합 터미널/Claude Code 데스크톱 앱을 써도 폰트·코드페이지 손 안 대고 잘 된다(더 간편).

---

## ⑦ 참조 출처 (공식 문서 · "버그 아니라 알려진 한계" 근거)

### Claude Code는 Windows Terminal 권장 — 공식
- **Claude Code 공식 터미널 가이드** — https://code.claude.com/docs/en/terminal-guide
  Windows Terminal + 네이티브 설치 권장, 유니코드 렌더링·복붙 문제를 Windows Terminal로 해결.

### 레거시 콘솔(conhost)의 한계 · Windows Terminal이 미래 — Microsoft 공식
- **Microsoft Dev Blog — Windows Terminal as your Default Command Line Experience**
  https://devblogs.microsoft.com/commandline/windows-terminal-as-your-default-command-line-experience/
  Win11 기본값이 Windows Terminal, 신기능은 Terminal에 집중·conhost는 호환성 유지용이라는 MS 입장.
- **Microsoft Learn — Windows Console Definitions**
  https://learn.microsoft.com/en-us/windows/console/definitions
  conhost는 API 처리만, UI는 pseudoconsole 통해 터미널에 위임 (= TUI는 터미널이 그려야 함).
- **microsoft/terminal (공식 저장소)** — https://github.com/microsoft/terminal

### D2Coding 한글 코딩폰트 — 공식 배포처
- **naver/d2codingfont** — https://github.com/naver/d2codingfont
  네이버 제작 무료 한글 고정폭 폰트. Releases 에서 다운로드.

> 정리: "Claude Code 화면 깨짐 / 한글 말썽"은 Claude Code·PowerShell의 결함이 아니라
> **Windows 터미널 생태계의 알려진 특성**이고(②가 근거), 해결책은 **Windows Terminal + D2Coding**(①③).

---

_작성: Claude Code · 이 파일은 개인 개발환경용이라 git에 커밋하지 않아도 됨._
