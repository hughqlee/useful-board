import datetime as dt

import pytest

from useful_board.store import Store

FUTURE = (dt.date.today() + dt.timedelta(days=90)).isoformat()


def make_item(item_id: str, **over):
    item = {
        "id": item_id,
        "question": f"{item_id} 질문",
        "metric": {"source": "manual", "unit": "원"},
        "compare": {"target": ">= 100", "warn": "< 50"},
        "action_rule": "50 미만이면 점검",
        "refresh": "daily",
        "review_by": FUTURE,
    }
    item.update(over)
    return item


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "board.db")
    s.goal_set({"id": "savings", "title": "비상금 1,000만 원", "due": "2027-12-31"})
    yield s
    s.close()
