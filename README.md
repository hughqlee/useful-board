# useful-board

목표를 세우고, 그 목표를 어떤 지표로 볼지 구체화하고, 정말 필요한 일이 진행되도록 돕는 보드.
별도 앱이나 창 없이 Claude 데스크톱 같은 에이전트 툴 안에서만 돈다.

- 설계: [docs/design.md](docs/design.md)
- 스펙 스키마: [src/useful_board/board.schema.json](src/useful_board/board.schema.json)
- 예시: [examples/savings.board.yaml](examples/savings.board.yaml)

## 핵심 규칙

1. 일하는 곳(에이전트 대화)에서만 보인다.
2. 목표 없이는 항목도 없다. 모든 항목에는 질문, 비교 기준, 행동 규칙, 검토일이 있다.
3. 보드 하나에 항목은 최대 7개. 정상은 접고 예외만 펼친다.
4. 지표가 경고선을 넘으면 할 일(Action)이 제안되고, 끝날 때까지 추적된다.

규칙은 서버 코드가 강제한다. 어기는 요청은 거부되고, 어떤 원칙 때문인지 사유가 돌아온다.

## 설치

[uv](https://docs.astral.sh/uv/)가 필요하다.

```sh
# 첫 셋업: ~/.useful-board/board.db 를 만들고 연결 설정을 보여 준다
uvx --from git+https://github.com/hughqlee/useful-board useful-board init
# 예시로 시작하려면
uvx --from git+https://github.com/hughqlee/useful-board useful-board init --import examples/savings.board.yaml
```

Claude 데스크톱의 `claude_desktop_config.json`에 추가:

```json
{
  "mcpServers": {
    "useful-board": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/hughqlee/useful-board", "useful-board"]
    }
  }
}
```

보드 위치는 `USEFUL_BOARD_HOME` 환경 변수로 바꿀 수 있다.

## MCP 도구

| 도구 | 하는 일 |
| --- | --- |
| `board_get` | 보드 카드(마크다운). 대화 안에 그대로 보여 준다 |
| `board_evaluate` | 항목 상태, 먼저 알릴 것, 할 일 제안, 검토할 항목 |
| `goal_set` | 목표 만들기·고치기 |
| `item_propose` | 항목 넣기·고치기. 원칙 2·3·6·7 검사 |
| `item_remove` | 항목 빼기 |
| `snapshot_record` | 값 기록(출처 포함) |
| `action_add` / `action_update` | 할 일 만들기·완료·취소 |
| `note_add` | 값에 메모("이 값은 왜?") |
| `board_export` / `board_import` | board.yaml 내보내기·가져오기 |

## 저장

SQLite 파일 하나(`board.db`). 서버 프로세스 하나만 열 수 있도록 잠금 파일로 막는다.
네트워크 드라이브나 동기화 폴더에 두지 않는다. WAL 모드, 하루 한 번 `backups/`에 스냅샷(최근 7개 유지).

## 개발

```sh
uv run --group dev pytest
```
