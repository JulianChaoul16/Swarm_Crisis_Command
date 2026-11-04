"""Incoming-report connectors: poll an external database for new reports and send each one
through normal intake (Intake Agent -> swarm vote -> planner -> human approval).

Configured in .env:

    CONNECTOR_SQL_URL=sqlite:///path/to/reports.db   # or postgresql://user:pass@host/db
    CONNECTOR_SQL_TABLE=incoming_reports
    CONNECTOR_POLL_SECONDS=5

The table needs columns ``id`` (increasing integer), ``text`` and ``source``. Rows are read
in id order, once each; the last id seen is kept in memory, so a restart re-reads from the
newest row present at startup rather than replaying history.

SQLite works out of the box. PostgreSQL needs ``pip install psycopg`` (not a dependency
otherwise). Any other source can implement ``Connector.poll``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from threading import Event, Thread
from typing import Any, Callable, Protocol
from urllib.parse import urlparse


@dataclass(frozen=True)
class IncomingReport:
    external_id: str
    text: str
    source: str


class Connector(Protocol):
    name: str

    def poll(self) -> list[IncomingReport]:
        """Reports that arrived since the last poll."""
        ...


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class SqlTableConnector:
    """Polls ``SELECT id, text, source FROM <table> WHERE id > last_id``."""

    url: str
    table: str
    name: str = "sql"
    start_from_latest: bool = True
    last_id: int | None = None
    _connect: Callable[[], Any] = field(init=False, repr=False)
    _placeholder: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not _IDENTIFIER.match(self.table):
            raise ValueError(f"Invalid table name: {self.table!r}")
        parsed = urlparse(self.url)
        if parsed.scheme == "sqlite":
            path = self.url.removeprefix("sqlite:///")
            self._connect, self._placeholder = (lambda: sqlite3.connect(path)), "?"
        elif parsed.scheme in ("postgres", "postgresql"):
            try:
                import psycopg  # type: ignore[import-not-found]
            except ImportError as error:
                raise RuntimeError("PostgreSQL connector needs: pip install psycopg") from error
            self._connect, self._placeholder = (lambda: psycopg.connect(self.url)), "%s"
        else:
            raise ValueError(f"Unsupported connector URL scheme: {parsed.scheme or '(none)'}")
        self.name = f"{parsed.scheme}:{self.table}"

    def _query(self, sql: str, params: tuple = ()) -> list[tuple]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(sql, params)
            return cursor.fetchall()
        finally:
            connection.close()

    def poll(self) -> list[IncomingReport]:
        if self.last_id is None:
            if self.start_from_latest:
                self.last_id = self._query(f"SELECT COALESCE(MAX(id), 0) FROM {self.table}")[0][0]
                return []
            self.last_id = 0
        rows = self._query(
            f"SELECT id, text, source FROM {self.table} WHERE id > {self._placeholder} ORDER BY id",
            (self.last_id,),
        )
        reports = []
        for row_id, text, source in rows:
            self.last_id = max(self.last_id, int(row_id))
            if text and str(text).strip():
                reports.append(IncomingReport(f"{self.name}#{row_id}", str(text).strip(),
                                              str(source or self.name).strip()))
        return reports


@dataclass
class ConnectorRunner:
    """Background thread: poll each connector, hand new reports to ``deliver``."""

    connectors: list[Connector]
    deliver: Callable[[IncomingReport], None]
    interval_s: float = 5.0
    on_error: Callable[[str, str], None] | None = None
    status: dict[str, dict[str, Any]] = field(default_factory=dict)
    _stop: Event = field(default_factory=Event, repr=False)
    _thread: Thread | None = field(default=None, repr=False)

    def poll_once(self) -> int:
        delivered = 0
        for connector in self.connectors:
            entry = self.status.setdefault(connector.name, {"received": 0, "error": None})
            try:
                for report in connector.poll():
                    self.deliver(report)
                    entry["received"] += 1
                    delivered += 1
                entry["error"] = None
            except Exception as error:  # noqa: BLE001 - one bad feed must not stop the others
                entry["error"] = f"{type(error).__name__}: {str(error)[:160]}"
                if self.on_error:
                    self.on_error(connector.name, entry["error"])
        return delivered

    def start(self) -> None:
        if self._thread is not None or not self.connectors:
            return

        def loop() -> None:
            while not self._stop.is_set():
                self.poll_once()
                self._stop.wait(self.interval_s)

        self._thread = Thread(target=loop, name="connectors", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def connectors_from_env(setting: Callable[[str], str]) -> list[Connector]:
    url, table = setting("CONNECTOR_SQL_URL"), setting("CONNECTOR_SQL_TABLE") or "incoming_reports"
    return [SqlTableConnector(url, table)] if url else []
