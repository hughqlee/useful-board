import datetime as dt

from useful_board.render import render_card
from useful_board.status import evaluate

from conftest import make_item

NOW = dt.datetime.now(dt.timezone.utc)


def statuses(result):
    return {e["id"]: e["status"] for g in result["goals"] for e in g["items"]}


def test_statuses(store):
    store.item_propose("savings", make_item("ok"))
    store.item_propose("savings", make_item("warn"))
    store.item_propose("savings", make_item("old"))
    store.item_propose("savings", make_item("none"))
    store.snapshot_record("ok", 150)
    store.snapshot_record("warn", 30)
    store.snapshot_record("old", 150, observed_at=(NOW - dt.timedelta(days=3)).isoformat())
    assert statuses(evaluate(store)) == {"warn": "attention", "old": "stale", "none": "unknown", "ok": "ok"}


def test_target_only_item_is_attention_when_missed(store):
    store.item_propose("savings", make_item("t", compare={"target": ">= 100"}))
    store.snapshot_record("t", 80)
    assert statuses(evaluate(store)) == {"t": "attention"}


def test_manual_refresh_never_goes_stale(store):
    store.item_propose("savings", make_item("m", refresh="manual"))
    store.snapshot_record("m", 150, observed_at=(NOW - dt.timedelta(days=300)).isoformat())
    assert statuses(evaluate(store)) == {"m": "ok"}


def test_attention_suggests_action_until_one_is_open(store):
    store.item_propose("savings", make_item("warn"))
    store.snapshot_record("warn", 30)
    result = evaluate(store)
    assert result["suggested_actions"] == [{"goal_id": "savings", "item_id": "warn", "text": "50 미만이면 점검"}]
    assert result["alerts"][0]["item_id"] == "warn"
    store.action_add("savings", "지출 점검", item_id="warn")
    assert evaluate(store)["suggested_actions"] == []


def test_review_marks_items_without_actions_for_removal(store):
    store.item_propose("savings", make_item("a"))
    later = NOW + dt.timedelta(days=200)
    review = evaluate(store, now=later)["review"]
    assert review and review[0]["removal_candidate"] is True


def test_vs_previous(store):
    store.item_propose("savings", make_item("w", compare={"vs_previous": "week"}, refresh="weekly"))
    store.snapshot_record("w", 70, observed_at=(NOW - dt.timedelta(days=8)).isoformat())
    store.snapshot_record("w", 90)
    entry = evaluate(store)["goals"][0]["items"][0]
    assert entry["previous"] == 70


def test_all_ok_card_is_one_line(store):
    store.item_propose("savings", make_item("ok"))
    store.snapshot_record("ok", 150)
    card = render_card(evaluate(store))
    assert "\n" not in card and "모두 정상" in card


def test_card_hides_stale_numbers_and_folds_ok(store):
    store.item_propose("savings", make_item("ok"))
    store.item_propose("savings", make_item("old"))
    store.item_propose("savings", make_item("warn"))
    store.snapshot_record("ok", 150)
    store.snapshot_record("old", 123456, observed_at=(NOW - dt.timedelta(days=3)).isoformat())
    store.snapshot_record("warn", 30)
    card = render_card(evaluate(store))
    assert "123,456" not in card
    assert "정상 1개" in card
    assert card.index("주의") < card.index("오래됨")
    assert "할 일 제안: 50 미만이면 점검" in card
