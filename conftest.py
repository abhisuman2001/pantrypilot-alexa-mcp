# conftest.py — project-level pytest configuration.
# Adds the project root to sys.path so that `import server` works
# regardless of where pytest is invoked from.
import sys
import os

# Insert the project root (the directory containing this file) at the
# front of sys.path.  This lets the tests do `import server` directly.
sys.path.insert(0, os.path.dirname(__file__))

import importlib

import pytest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Fresh SQLite DB + reload server for every test, in every test file,
    so tests never touch the real pantry.db."""
    db_path = str(tmp_path / "test_pantry.db")
    monkeypatch.setenv("PANTRY_DB", db_path)
    monkeypatch.setenv("DEFAULT_OWNER", "default")

    import server
    importlib.reload(server)
    monkeypatch.setattr(server, "DB", db_path)
    monkeypatch.setattr(server, "DEFAULT_OWNER", "default")
    return db_path
