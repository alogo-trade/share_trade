from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,14}$")
_ALLOWED_TYPES = {"STOCK", "ETF"}
_ALLOWED_EXCHANGES = {"NASDAQ", "NYSE", "NYSE_ARCA", "OTHER"}


def load_candidate_metadata(path: str | Path, symbol: str) -> dict[str, Any]:
    """Return curated metadata for a candidate symbol, if it exists."""
    target = str(symbol or "").strip().upper()
    if not target:
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    rows = payload.get("instruments", []) if isinstance(payload, dict) else []
    for row in rows:
        if isinstance(row, dict) and str(row.get("symbol", "")).strip().upper() == target:
            return dict(row)
    return {}


def save_verified_us_instrument(
    path: str | Path,
    instrument: dict[str, Any],
    *,
    user_confirmed: bool,
) -> dict[str, Any]:
    """Persist a user-attested Zerodha Global Stocks symbol as verified."""
    if user_confirmed is not True:
        raise ValueError("Explicit confirmation in Zerodha Kite is required.")
    symbol = str(instrument.get("symbol", "")).strip().upper()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("Enter a valid US ticker symbol (letters, digits, dot or hyphen).")

    kind = str(instrument.get("type", "")).strip().upper()
    if kind not in _ALLOWED_TYPES:
        raise ValueError("Instrument type must be STOCK or ETF.")

    exchange = str(instrument.get("primary_exchange", "")).strip().upper()
    if exchange not in _ALLOWED_EXCHANGES:
        raise ValueError("Select a supported US exchange label.")

    name = str(instrument.get("name") or symbol).strip()
    if not name:
        name = symbol

    now = datetime.now(timezone.utc).isoformat()
    row = {
        "symbol": symbol,
        "name": name,
        "type": kind,
        "primary_exchange": exchange,
        "zerodha_eligible": True,
        "eligibility_status": "ZERODHA_VERIFIED",
        "verified_on": now,
        "verification_method": "USER_CONFIRMED_IN_KITE_GLOBAL_STOCKS",
        "eligibility_notes": (
            "User manually confirmed that this exact symbol is searchable "
            "in their Zerodha Kite Global Stocks / International segment."
        ),
    }
    for key in ("category", "industry", "currency", "india_inx_exchange_supported"):
        if instrument.get(key) is not None:
            row[key] = instrument[key]

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "source": "User manually verified in Zerodha Kite Global Stocks",
        "verified_on": now,
        "instruments": [],
    }
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload.update(existing)
                if not isinstance(payload.get("instruments"), list):
                    payload["instruments"] = []
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError(f"Cannot read existing verified US universe: {exc}") from exc

    rows = [
        item for item in payload["instruments"]
        if isinstance(item, dict)
        and str(item.get("symbol", "")).strip().upper() != symbol
    ]
    rows.append(row)
    payload["source"] = "User manually verified in Zerodha Kite Global Stocks"
    payload["verified_on"] = now
    payload["instruments"] = sorted(rows, key=lambda item: str(item.get("symbol", "")))

    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)

    return row
