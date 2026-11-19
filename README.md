# useful-board

목표를 세우고, 그 목표를 어떤 지표로 볼지 구체화하고, 정말 필요한 일이 진행되도록 돕는 보드.
별도 앱이나 창 없이 Claude 데스크톱 같은 에이전트 툴 안에서만 돈다.

- 설계: [docs/design.md](docs/design.md)
- 스펙 스키마: [schema/board.schema.json](schema/board.schema.json)
- 예시: [examples/savings.board.yaml](examples/savings.board.yaml)

## 핵심 규칙

1. 일하는 곳(에이전트 대화)에서만 보인다.
2. 목표 없이는 항목도 없다. 모든 항목에는 질문, 비교 기준, 행동 규칙, 만료일이 있다.
3. 보드 하나에 항목은 최대 7개. 정상은 접고 예외만 펼친다.
4. 지표가 경고선을 넘으면 할 일(Action)이 만들어지고 끝날 때까지 추적된다.

## 구조

로컬 MCP 서버(코어) + SQLite 파일(서버 프로세스만 엶) + Claude 플러그인(목표 인터뷰, 주간 점검).

상태: 설계 단계. 구현 전.
