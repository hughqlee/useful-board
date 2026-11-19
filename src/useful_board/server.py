"""useful-board MCP 서버 (stdio).

실행:  useful-board            서버 시작(Claude 데스크톱 등 에이전트가 띄운다)
       useful-board init       첫 셋업. SQLite 파일을 만들고 연결 설정을 보여 준다
       useful-board export     board.yaml로 내보내기
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from typing import Any

import yaml
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .render import render_card
from .status import evaluate
from .store import Store, StoreLocked, default_home
from .validate import SpecError

INSTRUCTIONS = """\
useful-board는 목표를 세우고, 그 목표를 볼 지표를 정하고, 필요한 일이 진행되게 돕는 보드다.

- 보드는 board_get이 돌려주는 마크다운 카드를 대화 응답 안에 그대로 보여 준다.
  별도 창, 아티팩트, 웹 페이지, 패널로 만들지 않는다.
- 항목은 item_propose로만 넣는다. 거부되면 사유를 사용자에게 그대로 전하고, 질문·비교 기준·행동 규칙을
  함께 구체화한다. 항목은 보드 전체 7개까지다. 넣으려면 하나를 빼야 할 수 있다.
- board_evaluate 결과의 alerts가 있으면 사용자가 묻지 않아도 먼저 짧게 알린다.
  suggested_actions는 사용자에게 확인받은 뒤 action_add로 만든다.
- 값은 커넥터로 읽어 snapshot_record로 기록한다. 출처를 source_ref에 남긴다.
"""

mcp = MCPServer("useful-board", instructions=INSTRUCTIONS)
_store: Store | None = None
_lock = threading.Lock()  # SQLite 연결 하나를 도구 호출이 차례로 쓴다


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
        _store.backup_if_due()
    return _store


def _run(fn, *args):
    """fn은 Store 메서드 이름이거나 인자 없는 함수. 잠금 안에서 실행하고 규칙 위반은 도구 오류로 돌려준다."""
    try:
        with _lock:
            if isinstance(fn, str):
                return getattr(store(), fn)(*args)
            return fn(*args)
    except (SpecError, KeyError) as exc:
        raise ToolError(str(exc).strip("'\"")) from exc


@mcp.tool()
def board_get() -> str:
    """보드 카드를 마크다운으로 돌려준다. 대화 안에 그대로 보여 줄 것."""
    return _run(lambda: render_card(evaluate(store())))


@mcp.tool()
def board_evaluate() -> dict[str, Any]:
    """항목별 상태, 먼저 알릴 것(alerts), 할 일 제안, 검토할 항목을 구조화된 형태로 돌려준다."""
    return _run(lambda: evaluate(store()))


@mcp.tool()
def goal_set(id: str, title: str, why: str | None = None, due: str | None = None,
             state: str = "active") -> dict[str, Any]:
    """목표를 만들거나 고친다. id는 소문자·숫자·하이픈. due는 YYYY-MM-DD. state는 active, done, dropped."""
    goal = {"id": id, "title": title, "state": state}
    if why:
        goal["why"] = why
    if due:
        goal["due"] = due
    return _run("goal_set", goal)


@mcp.tool()
def item_propose(goal_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """목표에 항목을 넣거나 같은 id 항목을 고친다.

    item 필드: id, question, metric{source, compute?, unit}, compare{target?, warn?, vs_previous?},
    action_rule, refresh(hourly|daily|weekly|monthly|manual), review_by(YYYY-MM-DD), widget?.
    조건은 ">= 420000" 같은 형식. 규칙을 어기면 거부 사유를 돌려준다.
    """
    return _run("item_propose", goal_id, item)


@mcp.tool()
def item_remove(item_id: str) -> str:
    """항목을 뺀다. 그 항목의 값 기록과 메모도 함께 지워진다."""
    _run("item_remove", item_id)
    return f"'{item_id}'를 뺐습니다."


@mcp.tool()
def snapshot_record(item_id: str, value: float, observed_at: str | None = None,
                    source_ref: str | None = None) -> dict[str, Any]:
    """항목의 값을 기록한다. observed_at은 ISO 시각(생략하면 지금). source_ref에 출처를 남긴다."""
    return _run("snapshot_record", item_id, value, observed_at, source_ref)


@mcp.tool()
def action_add(goal_id: str, text: str, item_id: str | None = None, due: str | None = None) -> dict[str, Any]:
    """할 일을 만든다. 지표 때문에 생긴 일이면 item_id를 함께 준다."""
    return _run("action_add", goal_id, text, item_id, due)


@mcp.tool()
def action_update(action_id: int, state: str | None = None, text: str | None = None,
                  due: str | None = None) -> dict[str, Any]:
    """할 일 상태(open, done, dropped)나 내용, 마감을 바꾼다."""
    return _run("action_update", action_id, state, text, due)


@mcp.tool()
def note_add(item_id: str, text: str, snapshot_id: int | None = None) -> dict[str, Any]:
    """값에 메모를 단다("이 값은 왜?"). snapshot_id를 생략하면 가장 최근 값에 단다."""
    return _run("note_add", item_id, text, snapshot_id)


@mcp.tool()
def board_export() -> str:
    """보드를 board.yaml 형식으로 돌려준다. 검토나 다른 곳으로 옮길 때 쓴다."""
    return _run(lambda: yaml.safe_dump(store().export_spec(), allow_unicode=True, sort_keys=False))


@mcp.tool()
def board_import(spec_yaml: str) -> str:
    """board.yaml 전체를 검증한 뒤 보드를 그 내용으로 맞춘다. id가 같은 항목의 값 기록은 남는다."""
    try:
        spec = yaml.safe_load(spec_yaml)
    except yaml.YAMLError as exc:
        raise ToolError(f"YAML을 읽을 수 없습니다: {exc}") from exc
    _run(lambda: store().import_spec(spec))
    return _run(lambda: render_card(evaluate(store())))


def _claude_desktop_snippet() -> str:
    return json.dumps(
        {"mcpServers": {"useful-board": {
            "command": "uvx",
            "args": ["--from", "git+https://github.com/hughqlee/useful-board", "useful-board"],
        }}},
        indent=2,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="useful-board")
    sub = parser.add_subparsers(dest="cmd")
    init = sub.add_parser("init", help="첫 셋업: SQLite 파일을 만든다")
    init.add_argument("--import", dest="import_path", type=Path, help="가져올 board.yaml")
    sub.add_parser("export", help="board.yaml로 내보내기")
    args = parser.parse_args(argv)

    if args.cmd is None:
        mcp.run("stdio")
        return

    try:
        s = Store()
    except StoreLocked as exc:
        sys.exit(f"{exc}\n서버를 끈 뒤 다시 실행하거나, 에이전트에서 board_export/board_import 도구를 쓰세요.")
    try:
        if args.cmd == "init":
            if args.import_path:
                s.import_spec(yaml.safe_load(args.import_path.read_text(encoding="utf-8")))
            print(f"보드 파일: {s.path}")
            print(f"백업 폴더: {default_home() / 'backups'}")
            print("\nClaude 데스크톱 설정(claude_desktop_config.json)에 추가:")
            print(_claude_desktop_snippet())
        elif args.cmd == "export":
            print(yaml.safe_dump(s.export_spec(), allow_unicode=True, sort_keys=False), end="")
    except SpecError as exc:
        sys.exit(f"스펙 검증 실패:\n{exc}")
    finally:
        s.close()


if __name__ == "__main__":
    main()
