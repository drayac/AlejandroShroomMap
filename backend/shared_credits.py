"""One Gemini budget shared by every app that uses the same key (Naturalia and the Rock, Plant,
Shroom and Animal maps), kept in a small ledger table in Naturalia's database.

Every successful Gemini call, by any app, is recorded as (day, app, kind). Two limits then apply:
- the key's real daily limit (GEMINI_GLOBAL_LIMIT, 40) over all calls of all apps;
- for an app with a group quota (the old single-group apps: GEMINI_DAILY_LIMIT_PER_KEY), its group's
  calls by ANY app - so a fungus identified in Naturalia leaves the Shroom map one call fewer.
Naturalia itself has no group quota: it can use whatever is left of the global limit.

The same file is copied into each app. It is optional: without CREDITS_DATABASE_URL (or if the ledger
can't be reached) `enabled()` is False / calls raise, and the app falls back to counting its own calls.
"""
import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

URL = os.environ.get("CREDITS_DATABASE_URL", "").strip()
GLOBAL_LIMIT = int(os.environ.get("GEMINI_GLOBAL_LIMIT", "40"))
_engine = create_engine(URL, pool_pre_ping=True, pool_size=2, max_overflow=2) if URL else None

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_ledger (
    usage_date DATE NOT NULL,
    app VARCHAR NOT NULL,
    kind VARCHAR NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (usage_date, app, kind)
)"""


def enabled() -> bool:
    return _engine is not None


def _today():
    return datetime.now(timezone.utc).date()


def usage() -> tuple[int, dict[str, int]]:
    """(calls today by every app, calls today per kind)."""
    with _engine.connect() as conn:
        rows = conn.execute(
            text("SELECT kind, SUM(count) FROM ai_ledger WHERE usage_date = :d GROUP BY kind"), {"d": _today()}
        ).all()
    by_kind = {k: int(n) for k, n in rows}
    return sum(by_kind.values()), by_kind


def remaining(kind: str | None = None, kind_limit: int | None = None) -> int:
    """Calls still allowed today: what is left of the global limit, and - with a group quota -
    of that group's quota."""
    total, by_kind = usage()
    left = GLOBAL_LIMIT - total
    if kind is not None and kind_limit is not None:
        left = min(left, kind_limit - by_kind.get(kind, 0))
    return max(0, left)


def record(app: str, kind: str) -> None:
    with _engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ai_ledger (usage_date, app, kind, count) VALUES (:d, :a, :k, 1) "
                "ON CONFLICT (usage_date, app, kind) DO UPDATE SET count = ai_ledger.count + 1"
            ),
            {"d": _today(), "a": app, "k": kind},
        )
