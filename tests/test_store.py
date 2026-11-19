import datetime as dt

import pytest
import yaml

from useful_board.store import Store, StoreLocked
from useful_board.validate import SpecError

from conftest import make_item


def test_eighth_item_is_refused_until_one_is_removed(store):
    for i in range(7):
        store.item_propose("savings", make_item(f"i{i}"))
    with pytest.raises(SpecError, match="원칙 6"):
        store.item_propose("savings", make_item("i7"))
    store.item_remove("i0")
    store.item_propose("savings", make_item("i7"))
    assert store.active_item_count() == 7


def test_editing_an_existing_item_does_not_count_twice(store):
    for i in range(7):
        store.item_propose("savings", make_item(f"i{i}"))
    store.item_propose("savings", make_item("i3", question="바뀐 질문"))
    assert store.item("i3")["question"] == "바뀐 질문"


def test_review_date_must_be_in_the_future(store):
    with pytest.raises(SpecError, match="원칙 7"):
        store.item_propose("savings", make_item("a", review_by="2020-01-01"))


def test_reactivating_a_goal_respects_the_limit(store):
    store.goal_set({"id": "health", "title": "10kg 감량"})
    for i in range(4):
        store.item_propose("health", make_item(f"h{i}"))
    store.goal_set({"id": "health", "title": "10kg 감량", "state": "done"})
    for i in range(4):
        store.item_propose("savings", make_item(f"s{i}"))
    with pytest.raises(SpecError, match="원칙 6"):
        store.goal_set({"id": "health", "title": "10kg 감량", "state": "active"})


def test_second_process_cannot_open_the_same_file(tmp_path):
    first = Store(tmp_path / "b.db")
    try:
        with pytest.raises(StoreLocked):
            Store(tmp_path / "b.db")
    finally:
        first.close()
    Store(tmp_path / "b.db").close()  # 닫은 뒤에는 다시 열린다


def test_wal_mode_is_on(store):
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_export_import_round_trip_keeps_history(store):
    store.item_propose("savings", make_item("monthly"))
    store.snapshot_record("monthly", 120)
    spec = yaml.safe_load(yaml.safe_dump(store.export_spec(), allow_unicode=True))
    spec["goals"][0]["items"][0]["question"] = "고친 질문"
    store.import_spec(spec)
    assert store.item("monthly")["question"] == "고친 질문"
    assert len(store.snapshots("monthly")) == 1


def test_import_removes_items_missing_from_spec(store):
    store.item_propose("savings", make_item("a"))
    store.item_propose("savings", make_item("b"))
    spec = store.export_spec()
    spec["goals"][0]["items"] = [i for i in spec["goals"][0]["items"] if i["id"] == "a"]
    store.import_spec(spec)
    assert [i["id"] for i in store.items()] == ["a"]


def test_invalid_import_changes_nothing(store):
    store.item_propose("savings", make_item("a"))
    spec = store.export_spec()
    del spec["goals"][0]["items"][0]["action_rule"]
    with pytest.raises(SpecError):
        store.import_spec(spec)
    assert store.item("a")["action_rule"]


def test_action_lifecycle(store):
    store.item_propose("savings", make_item("a"))
    action = store.action_add("savings", "지출 점검", item_id="a")
    assert action["state"] == "open"
    done = store.action_update(action["id"], state="done")
    assert done["state"] == "done" and done["closed_at"]
    assert store.actions("open") == []


def test_note_attaches_to_latest_value(store):
    store.item_propose("savings", make_item("a"))
    snap = store.snapshot_record("a", 10)
    note = store.note_add("a", "보너스 달이라 높음")
    assert note["snapshot_id"] == snap["id"]


def test_daily_backup_keeps_seven(store):
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    for d in range(10):
        store.backup_if_due(base + dt.timedelta(days=d))
    assert store.backup_if_due(base + dt.timedelta(days=9)) is None
    assert len(list((store.path.parent / "backups").glob("*.db"))) == 7
