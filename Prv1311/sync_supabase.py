"""
================================================================================
FILE: sync_supabase.py
Pushes rider + scavenger + dogs + markov ledgers to Supabase.
================================================================================
REAL BUG, FOUND LIVE (2026-08-12): dog_state/scav_state/core_state/
markov_state had been frozen since 2026-08-04 while rider_decisions/
rider_cycles kept updating live for every fleet. Root cause: push_dogs/
push_scavengers/push_core/push_markov were only ever called from each
engine's own run_engine() (the standalone-service infinite-loop wrapper) --
never from run_cycle() itself. run_all.py drives SCAV/DOGS/CORE/MARKOV by
calling run_cycle() directly through its own generic worker() loop, which
never went through run_engine() and therefore never called the push
functions at all. Not a swallowed exception, not a Supabase/schema issue --
confirmed live: calling these same upserts by hand succeeds instantly.
Fixed in run_all.py (worker() now takes an optional push_fn, called after
every successful cycle) -- see docs/CHANGELOG.md 2026-08-12.

Separately (also found during that investigation): push_ledger()'s upsert
never set `updated_at` explicitly, so rider_state's `updated_at` column only
ever reflected the row's original creation, not its actual last write --
already flagged as unreliable in docs/SITE_HANDOFF.md ("do not build a
staleness indicator on this column"). rider_state's actual ledger CONTENT
was fine the whole time (confirmed via rider_team.log's "ledger pushed"
lines matching the live balance) -- this was a false alarm caused by a
cosmetic gap, not a real outage, but fixed anyway since it's what triggered
this whole investigation: `updated_at` is now set explicitly on every push,
for every table below, not just rider_state.

REAL BUG #2, FOUND WHILE VERIFYING THE FIX ABOVE (same day): the first
version of this fix printed a "pushed (checkmark)" success line INSIDE the
try block, using a literal unicode checkmark. On a non-UTF8 console (this
machine's raw terminal, cp1252) that print() itself raised
UnicodeEncodeError -- caught by the same function's own except clause, which
then reported the push as FAILED and logged a false ERROR row to
rider_health, even though the upsert had already committed successfully
(confirmed: rider_state's updated_at was fresh immediately afterward despite
the "failed" report). Fixed by determining success/failure from the upsert
alone, before any print, and using plain ASCII in every message -- a print
can no longer masquerade a real success as a failure.

Every push_* function returns True/False (previously returned nothing --
every caller that ignores the return value keeps working exactly as before)
and reports every attempt to rider_health under component='ledger_sync', so
this class of "still running, but nothing landing" failure is visible from
rider_health alone from now on -- it doesn't require noticing a frozen
`updated_at` column or grepping a log file.
================================================================================
"""
from datetime import datetime, timezone

from supabase_client import get_client, record_health

_client = get_client()

# Per-table consecutive-failure counters for the ledger_sync health rows --
# independent per table so one table's failure streak doesn't get muddled
# with another's (these run from different threads/processes).
_consecutive_failures = {}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _push(table, payload, label, on_conflict=None):
    """Shared by every push_* function below. Success/failure is decided by
    the upsert alone -- nothing printed or logged afterward can turn a real
    success into a reported failure (see REAL BUG #2 above). Returns
    True/False; reports every attempt to rider_health (component=ledger_sync)."""
    try:
        kwargs = {"on_conflict": on_conflict} if on_conflict else {}
        _client.table(table).upsert(payload, **kwargs).execute()
    except Exception as e:
        print(f"  [SUPABASE] {label} push failed: {e}")
        _consecutive_failures[table] = _consecutive_failures.get(table, 0) + 1
        record_health("ledger_sync", "ERROR", {"table": table, "error": str(e)},
                      _consecutive_failures[table])
        return False

    # Upsert already committed -- everything below is best-effort reporting,
    # never allowed to flip this into a failure.
    try:
        print(f"  [SUPABASE] {label} pushed OK")
    except Exception:
        pass
    _consecutive_failures[table] = 0
    try:
        record_health("ledger_sync", "OK", {"table": table}, 0)
    except Exception:
        pass
    return True


def push_ledger(state, table="rider_state", row_key=1, key_column="id"):
    """Additive: called with no extra args, upserts row id=1 exactly as
    before -- byte-identical to every existing call site (rider_team.py's
    live service, flare/rider_flare.py). A per-user caller passes its own
    table (a NEW table, never rider_state/rider_flare_state) plus row_key=
    the user's id and key_column="user_id" -- each user's ledger lands on
    its own row, keyed on its own column, so two users' writes can never
    touch the same row. key_column must be that table's actual primary
    key (or a uniquely-constrained column) for the upsert's ON CONFLICT
    target to resolve correctly -- see docs/GO_LIVE_AUTHORITY.md for the
    schema this requires; not created here, this is a code change only.
    Returns True/False; also reports to rider_health (component=ledger_sync)."""
    return _push(table, {key_column: row_key, "ledger": state, "updated_at": _now_iso()},
                 "ledger", on_conflict=key_column)


def push_scavengers(state):
    return _push("scav_state", {"id": 1, "ledger": state, "updated_at": _now_iso()}, "scavengers")


def push_dogs(state):
    return _push("dog_state", {"id": 1, "ledger": state, "updated_at": _now_iso()}, "dogs")


def push_markov(state):
    return _push("markov_state", {"id": 1, "ledger": state, "updated_at": _now_iso()}, "markov")


def push_ewma(state):
    return _push("ewma_state", {"id": 1, "ledger": state, "updated_at": _now_iso()}, "ewma")


def push_regime(state):
    return _push("regime_state", {"id": 1, "ledger": state, "updated_at": _now_iso()}, "regime")


def push_cheap_window(state):
    return _push("cheap_window_state", {"id": 1, "ledger": state, "updated_at": _now_iso()}, "cheap_window")


def push_obi(state):
    return _push("obi_state", {"id": 1, "ledger": state, "updated_at": _now_iso()}, "obi")


def push_core(state):
    return _push("core_state", {"id": 1, "ledger": state, "updated_at": _now_iso()}, "core")
