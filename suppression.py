"""Small JSON-backed unsubscribe/suppression list for Serenity Blooms."""

import json
from datetime import datetime, timezone
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parent / "data" / "unsubscribes.json"


def _load() -> dict:
    if not DATA_FILE.exists():
        return {"unsubscribed": []}
    with DATA_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("unsubscribed", []), list):
        raise ValueError("data/unsubscribes.json has an invalid format.")
    return data


def suppressed_addresses() -> set[str]:
    return {
        str(item.get("email", "")).strip().lower()
        for item in _load().get("unsubscribed", [])
        if isinstance(item, dict) and item.get("email")
    }


def add_unsubscribe(email: str) -> bool:
    email = email.strip().lower()
    data = _load()
    if email in suppressed_addresses():
        return False
    data.setdefault("unsubscribed", []).append(
        {"email": email, "timestamp": datetime.now(timezone.utc).isoformat()}
    )
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with DATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return True
