"""Small SQLite parsed-result cache with explicit expiry and fill coalescing."""

import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path


def cache_key(source_id, operation, arguments, parser_version):
    encoded = json.dumps(
        [source_id, operation, arguments, parser_version],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


class Cache:
    def __init__(self, path: Path, *, clock=time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, value TEXT NOT NULL, expires REAL NOT NULL)"
        )
        self.connection.commit()
        self.clock = clock
        self.locks = {}

    def get(self, key):
        row = self.connection.execute("SELECT value, expires FROM results WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        if row[1] <= self.clock():
            self.connection.execute("DELETE FROM results WHERE key=?", (key,))
            self.connection.commit()
            return None
        return json.loads(row[0])

    def put(self, key, value, ttl):
        self.connection.execute(
            "INSERT OR REPLACE INTO results VALUES (?, ?, ?)", (key, json.dumps(value), self.clock() + ttl)
        )
        self.connection.commit()

    @asynccontextmanager
    async def filling(self, key):
        entry = self.locks.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                del self.locks[key]

    def close(self):
        self.connection.close()
