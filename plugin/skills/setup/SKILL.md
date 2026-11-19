---
name: setup
description: useful-board 첫 셋업. 플러그인을 처음 설치했거나, 보드 도구가 응답하지 않거나, 사용자가 "보드 시작", "셋업", "처음부터"라고 할 때 쓴다.
---

# useful-board 첫 셋업

목표: 보드 파일이 준비됐는지 확인하고, 첫 목표 인터뷰로 넘어간다. 사용자에게 설정 화면이나 별도 창을 열게 하지 않는다.

## 순서

1. `board_get`을 부른다.
   - 응답이 오면 보드 파일(SQLite)은 서버가 첫 호출 때 이미 만들었다. 3단계로 간다.
   - 도구가 없거나 서버가 시작되지 않으면 2단계로 간다.
2. 서버를 띄우지 못한 경우. 가장 흔한 원인은 `uv`가 없는 것이다. 사용자에게 한 번만 안내한다.
   - macOS·Linux: 터미널에서 `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows: PowerShell에서 `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - 설치 후 Claude를 다시 시작하라고 말한다. 직접 명령을 실행할 수 있는 환경이면 사용자 동의를 받고 실행한다.
3. 보드가 비어 있으면("아직 목표가 없습니다") 바로 `goal-interview` 스킬로 첫 목표를 만든다.
   보드에 이미 목표가 있으면 카드를 보여 주고 `board-check`로 값을 갱신할지 묻는다.
4. 셋업이 끝나면 한 번만 제안한다: "매일 아침 보드를 확인하는 예약 작업을 만들어 둘까요?"
   사용자가 원하면 그 환경의 예약 작업 기능으로 매일 `board-check`를 실행하게 한다.
   예약 기능이 없는 환경이면 "보드 확인해 줘"라고 말하면 된다고만 알려 준다.

## 알아 둘 것

- 보드 파일 위치는 기본 `~/.useful-board/board.db`이고 `USEFUL_BOARD_HOME`으로 바꿀 수 있다.
- 이 파일은 서버 프로세스 하나만 연다. 네트워크 드라이브나 동기화 폴더로 옮기지 말라고 안내한다.
- 백업은 서버가 하루 한 번 `backups/`에 남긴다(최근 7개).
