"""Хранение состояния игроков в SQLite (одна строка JSON на пользователя)."""
import json
import sqlite3
from pathlib import Path


class Storage:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS players (id INTEGER PRIMARY KEY, state TEXT NOT NULL)")
        self.db.commit()

    def load(self, uid: int) -> dict | None:
        row = self.db.execute("SELECT state FROM players WHERE id = ?", (uid,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, uid: int, state: dict):
        self.db.execute(
            "INSERT INTO players (id, state) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state = excluded.state",
            (uid, json.dumps(state, ensure_ascii=False)),
        )
        self.db.commit()

    def delete(self, uid: int):
        self.db.execute("DELETE FROM players WHERE id = ?", (uid,))
        self.db.commit()
