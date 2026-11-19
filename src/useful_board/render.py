"""보드 카드 렌더.

에이전트 대화 안에 그대로 붙일 수 있는 마크다운 한 덩어리를 만든다. 별도 창은 없다.
정상 항목은 한 줄로 접고, 모두 정상이면 카드 전체가 한 줄이다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .status import parse_condition

LABEL = {"attention": "주의", "stale": "오래됨", "unknown": "값 없음"}
SPARK = "▁▂▃▄▅▆▇█"


def fmt_num(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def fmt_cond(cond: str) -> str:
    op, num = parse_condition(cond)
    symbol = {">=": "≥", "<=": "≤", "==": "=", ">": ">", "<": "<"}[op]
    return f"{symbol} {fmt_num(num)}"


def local_time(iso: str, with_date: bool = True) -> str:
    t = dt.datetime.fromisoformat(iso).astimezone()
    return t.strftime("%m-%d %H:%M" if with_date else "%H:%M")


def sparkline(values: list[float]) -> str:
    if len(values) < 3:
        return ""
    lo, hi = min(values), max(values)
    if hi == lo:
        return SPARK[3] * len(values)
    return "".join(SPARK[round((v - lo) / (hi - lo) * (len(SPARK) - 1))] for v in values)


def _compare_text(compare: dict[str, Any]) -> str:
    parts = []
    if "target" in compare:
        parts.append(f"목표 {fmt_cond(compare['target'])}")
    if "warn" in compare:
        parts.append(f"경고 {fmt_cond(compare['warn'])}")
    return ", ".join(parts)


def _item_line(e: dict[str, Any]) -> str:
    label = LABEL[e["status"]]
    if e["status"] == "unknown":
        return f"- **{label}** · {e['question']} (아직 기록된 값이 없음)"
    if e["status"] == "stale":
        return f"- **{label}** · {e['question']} (마지막 값 {local_time(e['observed_at'])}, 숫자는 숨김)"
    value = f"**{fmt_num(e['value'])}{e['unit']}**"
    extras = [x for x in (_compare_text(e["compare"]),) if x]
    if e.get("previous") is not None:
        delta = e["value"] - e["previous"]
        extras.append(f"직전 대비 {'+' if delta >= 0 else ''}{fmt_num(delta)}")
    spark = sparkline(e["trend"])
    line = f"- **{label}** · {e['question']} {value}"
    if extras:
        line += f" ({'; '.join(extras)})"
    if spark:
        line += f" {spark}"
    line += f" · {e['source']}, {local_time(e['observed_at'])}"
    return line


def render_card(result: dict[str, Any]) -> str:
    checked = local_time(result["checked_at"], with_date=False)
    goals = result["goals"]
    nothing_open = all(not g["actions"] for g in goals)
    all_ok = all(e["status"] == "ok" for g in goals for e in g["items"])

    if not goals:
        return f"**{result['board']}** · 아직 목표가 없습니다. 이루고 싶은 것 하나로 시작하세요."
    if all_ok and nothing_open and not result["review"]:
        return f"**{result['board']}** · 모두 정상, 마지막 확인 {checked}"

    suggested = {s["item_id"]: s["text"] for s in result["suggested_actions"]}
    lines = [f"**{result['board']}** · {checked} 확인"]
    for g in goals:
        head = f"\n**{g['title']}**"
        if g["due"]:
            days = (dt.date.fromisoformat(g["due"]) - dt.date.fromisoformat(result["checked_at"][:10])).days
            head += f" · 마감 {g['due']} (D{'-' if days >= 0 else '+'}{abs(days)})"
        lines.append(head)
        ok = [e for e in g["items"] if e["status"] == "ok"]
        for e in g["items"]:
            if e["status"] == "ok":
                continue
            lines.append(_item_line(e))
            if e["id"] in suggested:
                lines.append(f"  - 할 일 제안: {suggested[e['id']]}")
        if ok:
            lines.append(f"- 정상 {len(ok)}개: " + ", ".join(e["question"] for e in ok))
        for a in g["actions"]:
            due = f" (마감 {a['due']})" if a["due"] else ""
            lines.append(f"- [ ] #{a['id']} {a['text']}{due}")

    if result["review"]:
        lines.append("\n**검토할 항목**")
        for r in result["review"]:
            hint = "할 일을 한 번도 만들지 않았습니다. 빼는 것을 권합니다." if r["removal_candidate"] else "유지할지 정해 주세요."
            lines.append(f"- {r['item_id']}: 검토일 {r['review_by']} 지남. {hint}")
    return "\n".join(lines)
