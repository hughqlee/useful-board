"""SQLite 저장소.

규칙: 이 파일은 MCP 서버 프로세스 하나만 연다. 다른 사람과 에이전트는 서버를 통해서만
접근한다. 그래서 열 때 잠금 파일로 두 번째 프로세스를 막는다. WAL 모드를 켜고,
하루에 한 번 백업 스냅샷을 남긴다.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .validate import MAX_ITEMS, SpecError, check_board, goal_problems, item_problems, normalize

BACKUP_KEEP = 7

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goals (
  id         TEXT PRIMARY KEY,
  title      TEXT NOT NULL,
  why        TEXT,
  due        TEXT,
  state      TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'done', 'dropped')),
  position   INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
  id          TEXT PRIMARY KEY,
  goal_id     TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
  question    TEXT NOT NULL,
  metric      TEXT NOT NULL,
  compare     TEXT NOT NULL,
  action_rule TEXT NOT NULL,
  refresh     TEXT NOT NULL,
  review_by   TEXT NOT NULL,
  widget      TEXT,
  position    INTEGER NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
  id          INTEGER PRIMARY KEY,
  item_id     TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  value       REAL NOT NULL,
  observed_at TEXT NOT NULL,
  source_ref  TEXT
);
CREATE INDEX IF NOT EXISTS snapshots_item_time ON snapshots(item_id, observed_at);
CREATE TABLE IF NOT EXISTS actions (
  id         INTEGER PRIMARY KEY,
  goal_id    TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
  item_id    TEXT REFERENCES items(id) ON DELETE SET NULL,
  text       TEXT NOT NULL,
  due        TEXT,
  state      TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open', 'done', 'dropped')),
  created_at TEXT NOT NULL,
  closed_at  TEXT
);
CREATE TABLE IF NOT EXISTS notes (
  id          INTEGER PRIMARY KEY,
  item_id     TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
  text        TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
"""


def default_home() -> Path:
    return Path(os.environ.get("USEFUL_BOARD_HOME") or Path.home() / ".useful-board")


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


class StoreLocked(RuntimeError):
    pass


class _ProcessLock:
    """보드 파일 옆의 .lock 파일에 배타 잠금을 건다. 다른 프로세스가 이미 열었으면 실패한다."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def acquire(self) -> None:
        self._fh = open(self.path, "a+")
        try:
            import fcntl
        except ImportError:  # Windows: msvcrt로 같은 일을 한다
            import msvcrt

            try:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise StoreLocked(str(self.path)) from exc
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._fh.close()
            self._fh = None
            raise StoreLocked(
                f"다른 프로세스가 이미 이 보드를 열고 있습니다: {self.path}. "
                "SQLite 파일은 서버 프로세스 하나만 엽니다."
            ) from exc

    def release(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


class Store:
    def __init__(self, path: Path | str | None = None, *, lock: bool = True):
        self.path = Path(path) if path else default_home() / "board.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = _ProcessLock(self.path.with_suffix(".lock")) if lock else None
        if self._lock:
            self._lock.acquire()
        # 도구 호출이 작업 스레드에서 돌 수 있다. 동시 접근은 서버가 잠금으로 막는다.
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(SCHEMA_SQL)
        self.db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('board', '내 보드')")
        self.db.commit()

    def close(self) -> None:
        self.db.close()
        if self._lock:
            self._lock.release()

    # ---- 보드 메타 -------------------------------------------------------

    def board_meta(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM meta")}

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))
        self.db.commit()

    # ---- 목표 --------------------------------------------------------------

    def goal_set(self, goal: dict[str, Any]) -> dict[str, Any]:
        problems = goal_problems(goal)
        if problems:
            raise SpecError(problems)
        goal = normalize(goal)
        existing = self.db.execute("SELECT position FROM goals WHERE id = ?", (goal["id"],)).fetchone()
        if existing is None:
            position = self.db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM goals").fetchone()[0]
            self.db.execute(
                "INSERT INTO goals(id, title, why, due, state, position, created_at) VALUES (?,?,?,?,?,?,?)",
                (goal["id"], goal["title"], goal.get("why"), goal.get("due"), goal.get("state", "active"),
                 position, _iso(now_utc())),
            )
        else:
            new_state = goal.get("state", "active")
            if new_state == "active":
                self._check_capacity(adding=self._goal_item_count(goal["id"]), ignore_goal=goal["id"])
            self.db.execute(
                "UPDATE goals SET title=?, why=?, due=?, state=? WHERE id=?",
                (goal["title"], goal.get("why"), goal.get("due"), new_state, goal["id"]),
            )
        self.db.commit()
        return self.goal(goal["id"])

    def goal(self, goal_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if row is None:
            raise KeyError(f"목표 '{goal_id}'가 없습니다")
        return dict(row)

    def goals(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute("SELECT * FROM goals ORDER BY position")]

    # ---- 항목 ----------------------------------------------------------------

    def _goal_item_count(self, goal_id: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM items WHERE goal_id = ?", (goal_id,)).fetchone()[0]

    def active_item_count(self, ignore_goal: str | None = None, ignore_item: str | None = None) -> int:
        return self.db.execute(
            """SELECT COUNT(*) FROM items i JOIN goals g ON g.id = i.goal_id
               WHERE g.state = 'active' AND g.id IS NOT ? AND i.id IS NOT ?""",
            (ignore_goal, ignore_item),
        ).fetchone()[0]

    def _check_capacity(self, adding: int, ignore_goal: str | None = None, ignore_item: str | None = None) -> None:
        current = self.active_item_count(ignore_goal=ignore_goal, ignore_item=ignore_item)
        if current + adding > MAX_ITEMS:
            raise SpecError([
                f"원칙 6: 보드 항목은 최대 {MAX_ITEMS}개입니다. 지금 {current}개라 더 넣을 수 없습니다. "
                "덜 쓸모 있는 항목 하나를 item_remove로 빼면 넣을 수 있습니다."
            ])

    def item_propose(self, goal_id: str, item: dict[str, Any]) -> dict[str, Any]:
        """항목 추가 또는 같은 id 항목 수정. 원칙 2·3·6·7을 검사한다."""
        problems = item_problems(item)
        if problems:
            raise SpecError(problems)
        item = normalize(item)
        goal = self.goal(goal_id)
        if goal["state"] != "active":
            raise SpecError([f"목표 '{goal_id}'는 {goal['state']} 상태라 항목을 넣을 수 없습니다"])
        today = now_utc().date().isoformat()
        if item["review_by"] <= today:
            raise SpecError([f"원칙 7: review_by({item['review_by']})는 오늘 이후여야 합니다"])

        existing = self.db.execute("SELECT goal_id, position FROM items WHERE id = ?", (item["id"],)).fetchone()
        self._check_capacity(adding=1, ignore_item=item["id"] if existing else None)
        values = (
            goal_id, item["question"], json.dumps(item["metric"], ensure_ascii=False),
            json.dumps(item["compare"], ensure_ascii=False), item["action_rule"], item["refresh"],
            item["review_by"], item.get("widget"),
        )
        if existing:
            self.db.execute(
                """UPDATE items SET goal_id=?, question=?, metric=?, compare=?, action_rule=?, refresh=?,
                   review_by=?, widget=? WHERE id=?""",
                (*values, item["id"]),
            )
        else:
            position = self.db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM items").fetchone()[0]
            self.db.execute(
                """INSERT INTO items(goal_id, question, metric, compare, action_rule, refresh, review_by,
                   widget, id, position, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (*values, item["id"], position, _iso(now_utc())),
            )
        self.db.commit()
        return self.item(item["id"])

    def item_remove(self, item_id: str) -> None:
        cur = self.db.execute("DELETE FROM items WHERE id = ?", (item_id,))
        if cur.rowcount == 0:
            raise KeyError(f"항목 '{item_id}'가 없습니다")
        self.db.commit()

    @staticmethod
    def _item_row(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["metric"] = json.loads(d["metric"])
        d["compare"] = json.loads(d["compare"])
        return d

    def item(self, item_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(f"항목 '{item_id}'가 없습니다")
        return self._item_row(row)

    def items(self, goal_id: str | None = None) -> list[dict[str, Any]]:
        if goal_id is None:
            rows = self.db.execute("SELECT * FROM items ORDER BY position")
        else:
            rows = self.db.execute("SELECT * FROM items WHERE goal_id = ? ORDER BY position", (goal_id,))
        return [self._item_row(r) for r in rows]

    # ---- 값 ------------------------------------------------------------------

    def snapshot_record(self, item_id: str, value: float, observed_at: str | None = None,
                        source_ref: str | None = None) -> dict[str, Any]:
        self.item(item_id)
        when = dt.datetime.fromisoformat(observed_at) if observed_at else now_utc()
        if when.tzinfo is None:
            when = when.astimezone()  # 시간대 없는 값은 서버 컴퓨터의 현지 시각으로 본다
        cur = self.db.execute(
            "INSERT INTO snapshots(item_id, value, observed_at, source_ref) VALUES (?,?,?,?)",
            (item_id, float(value), _iso(when), source_ref),
        )
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM snapshots WHERE id = ?", (cur.lastrowid,)).fetchone())

    def snapshots(self, item_id: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM snapshots WHERE item_id = ? ORDER BY observed_at DESC, id DESC LIMIT ?",
            (item_id, limit),
        )
        return [dict(r) for r in rows]

    # ---- 할 일과 메모 -----------------------------------------------------------

    def action_add(self, goal_id: str, text: str, item_id: str | None = None, due: str | None = None) -> dict[str, Any]:
        self.goal(goal_id)
        if item_id is not None and self.item(item_id)["goal_id"] != goal_id:
            raise SpecError([f"항목 '{item_id}'는 목표 '{goal_id}'에 속하지 않습니다"])
        if not text.strip():
            raise SpecError(["할 일 내용이 비었습니다"])
        cur = self.db.execute(
            "INSERT INTO actions(goal_id, item_id, text, due, created_at) VALUES (?,?,?,?,?)",
            (goal_id, item_id, text.strip(), due, _iso(now_utc())),
        )
        self.db.commit()
        return self.action(cur.lastrowid)

    def action_update(self, action_id: int, state: str | None = None, text: str | None = None,
                      due: str | None = None) -> dict[str, Any]:
        current = self.action(action_id)
        if state is not None and state not in ("open", "done", "dropped"):
            raise SpecError([f"state는 open, done, dropped 중 하나여야 합니다: {state}"])
        new_state = state or current["state"]
        closed_at = current["closed_at"]
        if new_state != "open" and current["state"] == "open":
            closed_at = _iso(now_utc())
        elif new_state == "open":
            closed_at = None
        self.db.execute(
            "UPDATE actions SET state=?, text=?, due=?, closed_at=? WHERE id=?",
            (new_state, text if text is not None else current["text"], due if due is not None else current["due"],
             closed_at, action_id),
        )
        self.db.commit()
        return self.action(action_id)

    def action(self, action_id: int) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
        if row is None:
            raise KeyError(f"할 일 #{action_id}가 없습니다")
        return dict(row)

    def actions(self, state: str | None = "open") -> list[dict[str, Any]]:
        if state is None:
            rows = self.db.execute("SELECT * FROM actions ORDER BY id")
        else:
            rows = self.db.execute("SELECT * FROM actions WHERE state = ? ORDER BY id", (state,))
        return [dict(r) for r in rows]

    def action_count(self, item_id: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM actions WHERE item_id = ?", (item_id,)).fetchone()[0]

    def note_add(self, item_id: str, text: str, snapshot_id: int | None = None) -> dict[str, Any]:
        self.item(item_id)
        if snapshot_id is None:
            latest = self.snapshots(item_id, limit=1)
            snapshot_id = latest[0]["id"] if latest else None
        cur = self.db.execute(
            "INSERT INTO notes(item_id, snapshot_id, text, created_at) VALUES (?,?,?,?)",
            (item_id, snapshot_id, text, _iso(now_utc())),
        )
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone())

    def notes(self, item_id: str, limit: int = 3) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT * FROM notes WHERE item_id = ? ORDER BY id DESC LIMIT ?", (item_id, limit)
        )
        return [dict(r) for r in rows]

    # ---- board.yaml 내보내기·가져오기 ---------------------------------------------

    def export_spec(self) -> dict[str, Any]:
        meta = self.board_meta()
        spec: dict[str, Any] = {"board": meta.get("board", "내 보드")}
        if meta.get("owner"):
            spec["owner"] = meta["owner"]
        spec["goals"] = []
        for g in self.goals():
            goal = {k: g[k] for k in ("id", "title", "why", "due", "state") if g[k] is not None}
            goal["items"] = []
            for it in self.items(g["id"]):
                item = {k: it[k] for k in ("id", "question", "metric", "compare", "action_rule", "refresh",
                                            "review_by", "widget") if it[k] is not None}
                goal["items"].append(item)
            spec["goals"].append(goal)
        return spec

    def import_spec(self, spec: dict[str, Any]) -> None:
        """스펙 전체를 검증한 뒤 목표와 항목을 스펙과 같게 맞춘다.

        id가 같은 항목은 값 기록(Snapshot)과 메모가 그대로 남는다. 스펙에 없는 항목은 지운다.
        """
        spec = check_board(spec)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('board', ?)", (spec["board"],))
            if spec.get("owner"):
                self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('owner', ?)", (spec["owner"],))
            goal_ids = [g["id"] for g in spec["goals"]]
            item_ids = [i["id"] for g in spec["goals"] for i in g["items"]]
            self.db.execute(
                f"DELETE FROM items WHERE id NOT IN ({','.join('?' * len(item_ids))})", item_ids
            )
            self.db.execute(
                f"DELETE FROM goals WHERE id NOT IN ({','.join('?' * len(goal_ids))})", goal_ids
            )
            now = _iso(now_utc())
            for gpos, g in enumerate(spec["goals"]):
                self.db.execute(
                    """INSERT INTO goals(id, title, why, due, state, position, created_at) VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET title=excluded.title, why=excluded.why, due=excluded.due,
                       state=excluded.state, position=excluded.position""",
                    (g["id"], g["title"], g.get("why"), g.get("due"), g.get("state", "active"), gpos, now),
                )
            ipos = 0
            for g in spec["goals"]:
                for it in g["items"]:
                    self.db.execute(
                        """INSERT INTO items(id, goal_id, question, metric, compare, action_rule, refresh,
                           review_by, widget, position, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(id) DO UPDATE SET goal_id=excluded.goal_id, question=excluded.question,
                           metric=excluded.metric, compare=excluded.compare, action_rule=excluded.action_rule,
                           refresh=excluded.refresh, review_by=excluded.review_by, widget=excluded.widget,
                           position=excluded.position""",
                        (it["id"], g["id"], it["question"], json.dumps(it["metric"], ensure_ascii=False),
                         json.dumps(it["compare"], ensure_ascii=False), it["action_rule"], it["refresh"],
                         it["review_by"], it.get("widget"), ipos, now),
                    )
                    ipos += 1

    # ---- 백업 -----------------------------------------------------------------

    def backup_if_due(self, now: dt.datetime | None = None) -> Path | None:
        """오늘 백업이 없으면 하나 만들고, 최근 BACKUP_KEEP개만 남긴다."""
        now = now or now_utc()
        folder = self.path.parent / "backups"
        folder.mkdir(exist_ok=True)
        target = folder / f"{self.path.stem}-{now.strftime('%Y%m%d')}.db"
        if target.exists():
            return None
        dest = sqlite3.connect(target)
        try:
            self.db.backup(dest)
        finally:
            dest.close()
        for old in sorted(folder.glob(f"{self.path.stem}-*.db"))[:-BACKUP_KEEP]:
            old.unlink()
        return target
