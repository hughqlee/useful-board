from pathlib import Path

import yaml

from useful_board.validate import board_problems, item_problems

from conftest import make_item

EXAMPLE = Path(__file__).parent.parent / "examples" / "savings.board.yaml"


def test_example_board_is_valid():
    assert board_problems(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))) == []


def test_item_without_action_rule_is_rejected_with_principle():
    item = make_item("a")
    del item["action_rule"]
    problems = item_problems(item)
    assert len(problems) == 1 and "원칙 2" in problems[0]


def test_item_without_comparison_is_rejected():
    problems = item_problems(make_item("a", compare={}))
    assert any("원칙 3" in p for p in problems)


def test_bad_condition_format_is_rejected():
    assert item_problems(make_item("a", compare={"warn": "less than 5"}))


def test_board_over_seven_items_is_rejected():
    spec = {"board": "b", "goals": [
        {"id": "g1", "title": "t1", "items": [make_item(f"a{i}") for i in range(4)]},
        {"id": "g2", "title": "t2", "items": [make_item(f"b{i}") for i in range(4)]},
    ]}
    problems = board_problems(spec)
    assert any("원칙 6" in p and "8개" in p for p in problems)


def test_items_of_finished_goals_do_not_count():
    spec = {"board": "b", "goals": [
        {"id": "g1", "title": "t1", "state": "done", "items": [make_item(f"a{i}") for i in range(4)]},
        {"id": "g2", "title": "t2", "items": [make_item(f"b{i}") for i in range(4)]},
    ]}
    assert board_problems(spec) == []


def test_duplicate_item_ids_are_rejected():
    spec = {"board": "b", "goals": [
        {"id": "g1", "title": "t1", "items": [make_item("x")]},
        {"id": "g2", "title": "t2", "items": [make_item("x")]},
    ]}
    assert any("중복" in p for p in board_problems(spec))
