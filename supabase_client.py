"""
supabase_client.py — single shared Supabase client for PRV1311.

Wires the OS trust store once (this machine's AV performs TLS-inspection on
outbound HTTPS and re-signs with its own root cert, which certifi's public CA
bundle doesn't trust -- truststore verifies against the OS store instead,
where the AV root is already trusted). Credentials come from the environment
via python-dotenv, never from source, so key rotation is a .env edit, not a
code change.

Every module that talks to Supabase should import get_client() from here
instead of constructing its own client.
"""

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client

# Resolve .env next to this file, not relative to CWD. Under a Task Scheduler
# service running as SYSTEM (session 0), the working directory is not the
# repo, so a bare load_dotenv() would silently find nothing and this module
# would fall straight to the "not set" error below.
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_URL and/or SUPABASE_KEY are not set. Copy .env.example to "
        ".env in Prv1311/ and fill in real values."
    )

_client = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_client():
    return _client


def record_health(component, status, detail, consecutive_failures):
    """Best-effort health-row insert into rider_health. Never raises --
    a failure to report a failure must not also take down the caller."""
    try:
        _client.table("rider_health").insert({
            "component": component,
            "status": status,
            "detail": detail,
            "consecutive_failures": consecutive_failures,
        }).execute()
    except Exception:
        pass
