"""Small JSON persistence layer for local development and Cloud Run.

When SERENITY_DATA_BUCKET is set, JSON documents are stored in that private
Google Cloud Storage bucket. Otherwise, callers can keep using local files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def data_bucket_name() -> str:
    return os.getenv("SERENITY_DATA_BUCKET", "").strip()


def using_cloud_storage() -> bool:
    return bool(data_bucket_name())


def load_json(*, object_name: str, local_path: Path, default: Any) -> Any:
    """Load a JSON document from GCS when configured, otherwise from disk."""
    bucket_name = data_bucket_name()
    if bucket_name:
        from google.cloud import storage
        from google.api_core.exceptions import NotFound

        client = storage.Client()
        blob = client.bucket(bucket_name).blob(object_name)
        try:
            text = blob.download_as_text(encoding="utf-8")
        except NotFound:
            return default
        return json.loads(text)

    if not local_path.exists():
        return default
    with local_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(*, object_name: str, local_path: Path, value: Any) -> None:
    """Persist a JSON document to GCS when configured, otherwise to disk."""
    payload = json.dumps(value, indent=2) + "\n"
    bucket_name = data_bucket_name()
    if bucket_name:
        from google.cloud import storage

        client = storage.Client()
        blob = client.bucket(bucket_name).blob(object_name)
        blob.upload_from_string(payload, content_type="application/json")
        return

    local_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = local_path.with_suffix(local_path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        handle.write(payload)
    os.chmod(temp_path, 0o600)
    temp_path.replace(local_path)


def delete_json(*, object_name: str, local_path: Path) -> None:
    """Delete a persisted JSON document from the active backend."""
    bucket_name = data_bucket_name()
    if bucket_name:
        from google.cloud import storage
        from google.api_core.exceptions import NotFound

        client = storage.Client()
        blob = client.bucket(bucket_name).blob(object_name)
        try:
            blob.delete()
        except NotFound:
            pass
        return

    local_path.unlink(missing_ok=True)
