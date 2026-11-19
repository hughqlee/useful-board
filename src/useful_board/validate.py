"""스펙 검증기.

JSON Schema(board.schema.json)로 형태를 검사하고, 스키마로 표현할 수 없는
보드 단위 규칙(항목 합계 7개 이하, id 중복 금지)을 코드로 검사한다.
"""

from __future__ import annotations

import datetime as dt
import json
from importlib import resources
from typing import Any

import jsonschema

MAX_ITEMS = 7

SCHEMA: dict[str, Any] = json.loads(
    resources.files("useful_board").joinpath("board.schema.json").read_text(encoding="utf-8")
)

# 필수 필드가 빠졌을 때 어떤 원칙 때문인지 알려 준다.
_FIELD_REASON = {
    "question": "원칙 2: 질문 없는 항목은 만들 수 없습니다",
    "action_rule": "원칙 2: 경고선을 넘었을 때 무엇을 할지(action_rule) 없이는 항목을 만들 수 없습니다",
    "compare": "원칙 3: 목표치, 경고선, 직전 기간 비교 중 하나가 있어야 합니다",
    "review_by": "원칙 7: 모든 항목에는 다시 검토할 날짜(review_by)가 있어야 합니다",
    "metric": "값을 어디서 얻는지(metric.source, unit)가 있어야 합니다",
    "refresh": "갱신 주기(refresh)가 있어야 신선도를 판단할 수 있습니다",
}


class SpecError(ValueError):
    """검증 실패. problems에 사람이 읽을 사유가 하나씩 들어 있다."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


def normalize(value: Any) -> Any:
    """YAML이 날짜를 date 객체로 읽으므로 스펙 형식(ISO 문자열)으로 되돌린다."""
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def _validator(ref: str | None = None) -> jsonschema.Draft202012Validator:
    schema = SCHEMA if ref is None else {"$defs": SCHEMA["$defs"], "$ref": ref}
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def _messages(errors: list[jsonschema.ValidationError], prefix: str) -> list[str]:
    out: list[str] = []
    for err in sorted(errors, key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path)
        where = f"{prefix}.{path}" if path else prefix
        if err.validator == "required":
            missing = err.message.split("'")[1] if "'" in err.message else ""
            reason = _FIELD_REASON.get(missing, f"'{missing}' 필드가 필요합니다")
            out.append(f"{where}: {reason}")
        elif err.validator == "minProperties" and where.endswith("compare"):
            out.append(f"{where}: {_FIELD_REASON['compare']}")
        else:
            out.append(f"{where}: {err.message}")
    return out


def item_problems(item: dict[str, Any]) -> list[str]:
    return _messages(list(_validator("#/$defs/item").iter_errors(normalize(item))), "item")


def goal_problems(goal: dict[str, Any]) -> list[str]:
    """Goal 자체 필드만 검사한다. items는 item_propose로 하나씩 들어온다."""
    base = SCHEMA["$defs"]["goal"]
    fields_only = {
        **base,
        "required": [r for r in base["required"] if r != "items"],
        "properties": {k: v for k, v in base["properties"].items() if k != "items"},
    }
    validator = jsonschema.Draft202012Validator(
        {"$defs": SCHEMA["$defs"], **fields_only}, format_checker=jsonschema.FormatChecker()
    )
    goal = {k: v for k, v in normalize(goal).items() if k != "items"}
    return _messages(list(validator.iter_errors(goal)), "goal")


def board_problems(spec: dict[str, Any]) -> list[str]:
    spec = normalize(spec)
    problems = _messages(list(_validator().iter_errors(spec)), "board")
    if problems:
        return problems

    goal_ids: set[str] = set()
    item_ids: set[str] = set()
    active_items = 0
    for goal in spec["goals"]:
        if goal["id"] in goal_ids:
            problems.append(f"goal '{goal['id']}': id가 중복됩니다")
        goal_ids.add(goal["id"])
        for item in goal["items"]:
            if item["id"] in item_ids:
                problems.append(f"item '{item['id']}': id가 보드 안에서 중복됩니다")
            item_ids.add(item["id"])
        if goal.get("state", "active") == "active":
            active_items += len(goal["items"])

    if active_items > MAX_ITEMS:
        problems.append(
            f"원칙 6: 보드 항목은 최대 {MAX_ITEMS}개입니다. 지금 {active_items}개라 {active_items - MAX_ITEMS}개를 빼야 합니다"
        )
    return problems


def check_board(spec: dict[str, Any]) -> dict[str, Any]:
    problems = board_problems(spec)
    if problems:
        raise SpecError(problems)
    return normalize(spec)
