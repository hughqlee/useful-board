"""Status 계산.

마지막 값이 갱신 주기보다 오래되면 stale, 경고선을 넘으면 attention,
값이 없으면 unknown, 나머지는 ok. 결과에는 에이전트가 먼저 말해야 할 것(alerts)과
만들어야 할 할 일 제안(suggested_actions), 검토할 항목(review)이 함께 들어간다.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .store import Store, now_utc

ORDER = {"attention": 0, "stale": 1, "unknown": 2, "ok": 3}

REFRESH_INTERVAL = {
    "hourly": dt.timedelta(hours=1),
    "daily": dt.timedelta(days=1),
    "weekly": dt.timedelta(days=7),
    "monthly": dt.timedelta(days=31),
    "manual": None,
}
# 예약 실행이 조금 늦어도 바로 "오래됨"이 되지 않게 주기의 1.5배까지 허용한다.
STALE_FACTOR = 1.5

PERIOD = {
    "day": dt.timedelta(days=1),
    "week": dt.timedelta(days=7),
    "month": dt.timedelta(days=30),
    "quarter": dt.timedelta(days=91),
    "year": dt.timedelta(days=365),
}

_COND = re.compile(r"^(>=|<=|>|<|==)\s*(-?\d+(?:\.\d+)?)$")


def parse_condition(cond: str) -> tuple[str, float]:
    m = _COND.match(cond.strip())
    if not m:
        raise ValueError(f"조건 형식이 아닙니다: {cond!r}")
    return m.group(1), float(m.group(2))


def holds(value: float, cond: str) -> bool:
    op, num = parse_condition(cond)
    return {
        ">=": value >= num,
        "<=": value <= num,
        ">": value > num,
        "<": value < num,
        "==": value == num,
    }[op]


def item_status(item: dict[str, Any], latest: dict[str, Any] | None, now: dt.datetime) -> str:
    if latest is None:
        return "unknown"
    interval = REFRESH_INTERVAL[item["refresh"]]
    observed = dt.datetime.fromisoformat(latest["observed_at"])
    if interval is not None and now - observed > interval * STALE_FACTOR:
        return "stale"
    compare = item["compare"]
    value = latest["value"]
    if "warn" in compare and holds(value, compare["warn"]):
        return "attention"
    if "target" in compare and "warn" not in compare and not holds(value, compare["target"]):
        return "attention"
    return "ok"


def previous_value(history: list[dict[str, Any]], period: str) -> float | None:
    """가장 최근 값보다 period 이상 앞선 값 중 가장 가까운 것."""
    if not history:
        return None
    latest_at = dt.datetime.fromisoformat(history[0]["observed_at"])
    for snap in history[1:]:
        if latest_at - dt.datetime.fromisoformat(snap["observed_at"]) >= PERIOD[period]:
            return snap["value"]
    return None


def evaluate(store: Store, now: dt.datetime | None = None) -> dict[str, Any]:
    now = now or now_utc()
    today = now.date().isoformat()
    open_actions = store.actions("open")
    goals_out: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    suggested: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []

    for goal in store.goals():
        if goal["state"] != "active":
            continue
        items_out = []
        for item in store.items(goal["id"]):
            history = store.snapshots(item["id"], limit=60)
            latest = history[0] if history else None
            status = item_status(item, latest, now)
            entry = {
                "id": item["id"],
                "question": item["question"],
                "status": status,
                "unit": item["metric"]["unit"],
                "source": item["metric"]["source"],
                "compare": item["compare"],
                "action_rule": item["action_rule"],
                "value": latest["value"] if latest and status != "stale" else None,
                "observed_at": latest["observed_at"] if latest else None,
                "trend": [s["value"] for s in reversed(history[:12])],
            }
            period = item["compare"].get("vs_previous")
            if period and latest and status != "stale":
                prev = previous_value(history, period)
                entry["previous"] = prev
            items_out.append(entry)

            if status in ("attention", "stale"):
                alerts.append({"item_id": item["id"], "status": status, "question": item["question"]})
            has_open = any(a["item_id"] == item["id"] for a in open_actions)
            if status == "attention" and not has_open:
                suggested.append({"goal_id": goal["id"], "item_id": item["id"], "text": item["action_rule"]})
            if item["review_by"] <= today:
                review.append({
                    "item_id": item["id"],
                    "review_by": item["review_by"],
                    "made_actions": store.action_count(item["id"]),
                    "removal_candidate": store.action_count(item["id"]) == 0,
                })

        items_out.sort(key=lambda e: ORDER[e["status"]])
        goals_out.append({
            "id": goal["id"],
            "title": goal["title"],
            "due": goal["due"],
            "items": items_out,
            "actions": [a for a in open_actions if a["goal_id"] == goal["id"]],
        })

    return {
        "board": store.board_meta().get("board", "내 보드"),
        "checked_at": now.isoformat(timespec="seconds"),
        "goals": goals_out,
        "alerts": alerts,
        "suggested_actions": suggested,
        "review": review,
    }
