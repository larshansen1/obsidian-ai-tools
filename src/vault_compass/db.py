"""Shared access to compass.duckdb inside one process.

DuckDB refuses a second connection to the same file with a different
read-only setting while one is open. The API reads, the watcher refreshes and
usage logging writes, all in one process, so each takes this lock for as long
as its connection is open.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

DB_LOCK = threading.RLock()


@contextmanager
def writable(db_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open compass.duckdb for writing (creating the folder if needed), then close it."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with DB_LOCK:
        con = duckdb.connect(str(db_path))
        try:
            yield con
        finally:
            con.close()


@contextmanager
def readonly(db_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open compass.duckdb read-only, then close it. The file must exist."""
    with DB_LOCK:
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            yield con
        finally:
            con.close()
