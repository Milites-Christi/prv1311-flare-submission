"""
================================================================================
FILE: sync_supabase.py
Pushes rider + scavenger + dogs + markov ledgers to Supabase.
================================================================================
"""
from supabase_client import get_client

_client = get_client()


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
    schema this requires; not created here, this is a code change only."""
    try:
        _client.table(table).upsert(
            {key_column: row_key, "ledger": state}, on_conflict=key_column
        ).execute()
        print("  [SUPABASE] ledger pushed ✓")
    except Exception as e:
        print(f"  [SUPABASE] push failed: {e}")


def push_scavengers(state):
    try:
        _client.table("scav_state").upsert({"id": 1, "ledger": state}).execute()
        print("  [SUPABASE] scavengers pushed ✓")
    except Exception as e:
        print(f"  [SUPABASE] scav push failed: {e}")


def push_dogs(state):
    try:
        _client.table("dog_state").upsert({"id": 1, "ledger": state}).execute()
        print("  [SUPABASE] dogs pushed ✓")
    except Exception as e:
        print(f"  [SUPABASE] dogs push failed: {e}")


def push_markov(state):
    try:
        _client.table("markov_state").upsert({"id": 1, "ledger": state}).execute()
        print("  [SUPABASE] markov pushed ✓")
    except Exception as e:
        print(f"  [SUPABASE] markov push failed: {e}")

def push_ewma(state):
    try:
        _client.table("ewma_state").upsert({"id": 1, "ledger": state}).execute()
        print("  [SUPABASE] ewma pushed ✓")
    except Exception as e:
        print(f"  [SUPABASE] ewma push failed: {e}")        
def push_regime(state):
    try:
        _client.table("regime_state").upsert({"id": 1, "ledger": state}).execute()
        print("  [SUPABASE] regime pushed ✓")
    except Exception as e:
        print(f"  [SUPABASE] regime push failed: {e}")        
def push_cheap_window(state):
    try:
        _client.table("cheap_window_state").upsert({"id": 1, "ledger": state}).execute()
        print("  [SUPABASE] cheap_window pushed ✓")
    except Exception as e:
        print(f"  [SUPABASE] cheap_window push failed: {e}")    
def push_obi(state):
    try:
        _client.table("obi_state").upsert({"id": 1, "ledger": state}).execute()
        print("  [SUPABASE] obi pushed ✓")
    except Exception as e:
        print(f"  [SUPABASE] obi push failed: {e}")          
def push_core(state):
    try:
        _client.table("core_state").upsert({"id": 1, "ledger": state}).execute()
        print("  [SUPABASE] core pushed ✓")
    except Exception as e:
        print(f"  [SUPABASE] core push failed: {e}")        