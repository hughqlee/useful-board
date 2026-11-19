import asyncio

import pytest

from mcp.server.mcpserver.exceptions import ToolError

from useful_board import server

TOOLS = {
    "board_get", "board_evaluate", "goal_set", "item_propose", "item_remove", "snapshot_record",
    "action_add", "action_update", "note_add", "board_export", "board_import",
}


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("USEFUL_BOARD_HOME", str(tmp_path))
    server._store = None
    yield
    if server._store:
        server._store.close()
    server._store = None


def call(name, args):
    return asyncio.run(server.mcp.call_tool(name, args))


def text(result):
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def test_tools_are_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert names == TOOLS


def test_rule_violation_returns_reason_as_tool_error():
    # 클라이언트에는 isError 결과로 전달된다. 서버 객체를 직접 부르면 ToolError로 올라온다.
    call("goal_set", {"id": "savings", "title": "비상금"})
    with pytest.raises(ToolError, match="원칙 2"):
        call("item_propose", {"goal_id": "savings", "item": {"id": "x", "question": "q", "compare": {}}})


def test_end_to_end_card():
    from conftest import make_item

    call("goal_set", {"id": "savings", "title": "비상금"})
    assert not call("item_propose", {"goal_id": "savings", "item": make_item("monthly")}).is_error
    call("snapshot_record", {"item_id": "monthly", "value": 30, "source_ref": "가계부"})
    card = text(call("board_get", {}))
    assert "주의" in card and "할 일 제안" in card


def test_first_setup_creates_database(tmp_path, capsys):
    server.main(["init"])
    out = capsys.readouterr().out
    assert (tmp_path / "board.db").exists()
    assert "mcpServers" in out
