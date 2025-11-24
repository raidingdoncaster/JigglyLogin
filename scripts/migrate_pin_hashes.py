#!/usr/bin/env python
"""
Backfill salted PIN hashes without forcing every trainer to reset their PIN.

Usage:
    python scripts/migrate_pin_hashes.py

Environment variables required:
    SUPABASE_URL
    SUPABASE_KEY
    PIN_SALT           (optional – defaults match app.py)
"""

from __future__ import annotations

import os
import sys
from typing import Dict

try:
    from supabase import create_client  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise SystemExit("supabase-py is required. Run `pip install -r requirements.txt`.") from exc

from app import _pin_hash_value, hash_value  # reuse existing helpers


def _build_unsalted_lookup() -> Dict[str, str]:
    """Precompute every 4-digit PIN → legacy sha256 hex digest."""
    lookup: Dict[str, str] = {}
    for value in range(10_000):
        pin = f"{value:04d}"
        lookup[hash_value(pin)] = pin
    return lookup


def migrate_all():
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if not (supabase_url and supabase_key):
        raise SystemExit("Missing SUPABASE_URL or SUPABASE_KEY environment variables.")

    client = create_client(supabase_url, supabase_key)
    lookup = _build_unsalted_lookup()

    print("🔍 Fetching trainer records...")
    resp = (
        client.table("sheet1")
        .select("id,trainer_username,pin_hash")
        .limit(5000)
        .execute()
    )
    rows = resp.data or []
    print(f"➡️ Retrieved {len(rows)} rows to inspect.")

    migrated = 0
    already_salted = 0
    missing_hash = 0

    for row in rows:
        trainer = (row.get("trainer_username") or "").strip()
        legacy_hash = (row.get("pin_hash") or "").strip()
        row_id = row.get("id")

        if not trainer or not legacy_hash:
            missing_hash += 1
            continue

        legacy_pin = lookup.get(legacy_hash)
        if not legacy_pin:
            already_salted += 1
            continue

        new_hash = _pin_hash_value(trainer, legacy_pin)
        if new_hash == legacy_hash:
            already_salted += 1
            continue

        print(f"  • Migrating {trainer} ({row_id})")
        client.table("sheet1").update({"pin_hash": new_hash}).eq("id", row_id).execute()
        migrated += 1

    print("\n✅ Migration complete.")
    print(f"    Migrated:       {migrated}")
    print(f"    Already salted: {already_salted}")
    print(f"    Missing hash:   {missing_hash}")

    if migrated == 0:
        print("    No updates were necessary.")


if __name__ == "__main__":
    try:
        migrate_all()
    except KeyboardInterrupt:
        sys.exit("\n⚠️ Migration cancelled by user.")
