from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from config import settings

ACTIVE = ("prepared", "unknown", "new", "accepted", "pending_new", "partially_filled",
          "pending_cancel", "pending_replace", "accepted_for_bidding", "stopped",
          "suspended", "calculated", "done_for_day", "held")


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.database, autocommit=True, row_factory=dict_row, connect_timeout=5)


def initialize():
    with connect() as db, db.transaction():
        db.execute("SELECT pg_advisory_xact_lock(61413)")
        db.execute(Path(__file__).with_name("schema.sql").read_text())


def event(db, cycle, kind, data):
    db.execute("INSERT INTO events(mode, cycle_id, kind, data) VALUES (%s,%s,%s,%s)",
               (settings.mode, cycle, kind, Jsonb(data)))


def step(db, cycle, name, state="done", detail=None):
    data = {"state": state, "detail": detail}
    with db.transaction():
        db.execute("UPDATE cycles SET steps = steps || %s WHERE id=%s",
                   (Jsonb({name: data}), cycle))
        event(db, cycle, "step", {"step": name, **data})
