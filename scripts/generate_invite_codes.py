#!/usr/bin/env python3
"""
generate_invite_codes — Phase 4.4.

Generate N invite codes for a tenant and write them as a CSV. Optionally
POSTs to the identity service's `/admin/invites/batch` so they're
immediately redeemable, or emits SQL for an out-of-band insert.

Usage
-----

    # Mint 50 codes locally (no network), save to a CSV:
    python3 scripts/generate_invite_codes.py \\
        --tenant tnt_pilot_mx --count 50 --label-prefix may-batch \\
        --out invites-may.csv

    # Same, but post to the running identity service:
    python3 scripts/generate_invite_codes.py \\
        --tenant tnt_pilot_mx --count 50 --label-prefix may-batch \\
        --identity-url http://localhost:8002 \\
        --admin-token "$IDENTITY_ADMIN_JWT" \\
        --out invites-may.csv

    # Generate locally + emit SQL the ops team can run:
    python3 scripts/generate_invite_codes.py \\
        --tenant tnt_pilot_mx --count 100 --label-prefix beta-w1 \\
        --sql invites-w1.sql

CSV columns:  code,tenant_id,label,role,expires_at_iso

The generator is the ONLY caller of `generate_invite_code()` outside the
running service. The collision rate at N=500 within a single tenant is
about 1 in 10**11 given the 30-char Crockford-base32 alphabet × 8 chars
(~26 bits per 4-char block × 2 blocks).
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow importing services/identity/store.py without installing the service.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "identity"))

from store import generate_invite_code, normalize_invite_code  # noqa: E402


def _post_batch(url: str, token: str, tenant_id: str, count: int,
                label_prefix: str, role: str,
                ttl_seconds: int | None, notes: str) -> list[dict]:
    """Use the identity service's batch endpoint so the codes are stored
    server-side and immediately validatable."""
    import urllib.request, json as _json    # noqa: E401
    body = _json.dumps({
        "tenant_id": tenant_id,
        "count": count,
        "label_prefix": label_prefix,
        "role": role,
        "ttl_seconds": ttl_seconds,
        "notes": notes,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{url.rstrip('/')}/admin/invites/batch",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = _json.loads(resp.read().decode("utf-8"))
    return payload.get("codes", [])


def _generate_local(count: int, tenant_id: str, label_prefix: str,
                    role: str, ttl_seconds: int | None,
                    notes: str) -> list[dict]:
    now = time.time()
    expires_at = (now + ttl_seconds) if ttl_seconds else None
    out: list[dict] = []
    seen: set[str] = set()
    while len(out) < count:
        c = generate_invite_code()
        if c in seen:
            continue
        seen.add(c)
        out.append({
            "code": c,
            "tenant_id": tenant_id,
            "label": f"{label_prefix}-{len(out) + 1:03d}",
            "role": role,
            "expires_at": expires_at,
            "notes": notes,
        })
    return out


def _write_csv(path: Path, codes: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "tenant_id", "label", "role", "expires_at_iso", "notes"])
        for c in codes:
            iso = ""
            if c.get("expires_at"):
                iso = datetime.fromtimestamp(
                    float(c["expires_at"]), tz=timezone.utc
                ).isoformat()
            w.writerow([c["code"], c["tenant_id"], c["label"],
                        c["role"], iso, c.get("notes", "")])


def _write_sql(path: Path, codes: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("-- Casa·Orquesta invite codes — generated "
                f"{datetime.now(timezone.utc).isoformat()}\n")
        f.write("BEGIN;\n")
        for c in codes:
            exp = ("NULL" if not c.get("expires_at")
                   else f"to_timestamp({float(c['expires_at']):.0f})")
            notes = (c.get("notes", "") or "").replace("'", "''")
            f.write(
                "INSERT INTO invite_codes "
                "(code, tenant_id, label, role, expires_at, notes) VALUES "
                f"('{c['code']}', '{c['tenant_id']}', '{c['label']}', "
                f"'{c['role']}', {exp}, '{notes}');\n"
            )
        f.write("COMMIT;\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate invite codes.")
    p.add_argument("--tenant", required=True,
                   help="Tenant id, e.g. tnt_pilot_mx")
    p.add_argument("--count", type=int, default=10,
                   help="How many codes to generate (1–500).")
    p.add_argument("--label-prefix", default="batch",
                   help="Ops-friendly label prefix; suffix is -001, -002…")
    p.add_argument("--role", default="buyer",
                   choices=["buyer", "broker", "admin"])
    p.add_argument("--ttl-days", type=int, default=None,
                   help="Expire codes after this many days (default: never).")
    p.add_argument("--notes", default="",
                   help="Free-form ops notes (CSV column).")
    p.add_argument("--out", type=Path, default=None,
                   help="CSV output path. Required unless --sql or --identity-url is set.")
    p.add_argument("--sql", type=Path, default=None,
                   help="If set, also emit a Postgres INSERT script here.")
    p.add_argument("--identity-url", default=None,
                   help="If set, POST to identity-service /admin/invites/batch.")
    p.add_argument("--admin-token", default=None,
                   help="Bearer token for the admin endpoint.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the codes; don't write files or POST.")
    args = p.parse_args(argv)

    if not (args.out or args.sql or args.identity_url or args.dry_run):
        p.error("specify at least one of --out / --sql / --identity-url / --dry-run")
    if args.count < 1 or args.count > 500:
        p.error("--count must be between 1 and 500")

    ttl_seconds = int(args.ttl_days * 86400) if args.ttl_days else None

    if args.identity_url:
        if not args.admin_token:
            p.error("--admin-token is required with --identity-url")
        codes = _post_batch(
            args.identity_url, args.admin_token,
            args.tenant, args.count, args.label_prefix, args.role,
            ttl_seconds, args.notes,
        )
    else:
        codes = _generate_local(
            args.count, args.tenant, args.label_prefix, args.role,
            ttl_seconds, args.notes,
        )

    # Sanity-check the format on the way out.
    for c in codes:
        if normalize_invite_code(c["code"]) != c["code"]:
            print(f"  ⚠️  code {c['code']!r} doesn't normalize to itself",
                  file=sys.stderr)

    if args.dry_run:
        for c in codes:
            print(f"  {c['code']}  {c['label']}  role={c['role']}")
        return 0

    if args.out:
        _write_csv(args.out, codes)
        print(f"  wrote {len(codes)} codes → {args.out}")
    if args.sql:
        _write_sql(args.sql, codes)
        print(f"  wrote SQL → {args.sql}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
