# email_source.py
"""
Email source abstraction.

Two implementations:
- FileBasedEmailSource: reads from local JSON files. Used for development and
  evals where reproducibility matters.
- APIBasedEmailSource: calls the live email API. Used in production and for
  occasional live verification.

Both expose the same Protocol interface. Agent code uses whichever is
configured via EMAIL_SOURCE env var.
"""

import json
import os
from pathlib import Path
from typing import Any, Optional, Protocol

import requests
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# Protocol — what every email source must implement
# ============================================================

class EmailSource(Protocol):
    """Read-only access to emails and their attachments."""

    def get_email(self, message_id: str) -> dict[str, Any]:
        """Fetch a single email by message_id. Returns the email JSON dict."""
        ...

    def list_attachments(self, message_id: str) -> list[dict[str, Any]]:
        """List attachment metadata for an email. Returns list of attachment dicts."""
        ...

    def fetch_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Fetch raw attachment bytes. Returns the binary content."""
        ...

    def list_known_message_ids(self) -> list[str]:
        """List all message_ids this source knows about. For evals and exploration."""
        ...


class EmailSourceError(Exception):
    """Raised when an email source operation fails."""


# ============================================================
# File-based source — used for development and evals
# ============================================================

class FileBasedEmailSource:
    """Reads emails from local JSON files in a directory.

    File naming convention: any `*.json` file in the directory is treated as
    an email. The `id` field of each JSON is the message_id used for lookup.

    Attachments: this source does not support attachments — the JSON files
    don't contain attachment content (they're metadata only). Attempts to
    fetch attachments raise EmailSourceError. For attachment work, use
    APIBasedEmailSource.
    """

    def __init__(self, samples_dir: str | Path):
        self.samples_dir = Path(samples_dir)
        if not self.samples_dir.exists():
            raise EmailSourceError(
                f"Samples directory does not exist: {self.samples_dir}. "
                f"Make sure your real samples are in this folder."
            )
        # Build message_id → file path index on init
        self._index: dict[str, Path] = {}
        for json_file in sorted(self.samples_dir.glob("*.json")):
            try:
                with open(json_file) as f:
                    data = json.load(f)
                if "id" in data:
                    self._index[data["id"]] = json_file
            except Exception as e:
                # Skip malformed files but don't crash
                print(f"Warning: skipped {json_file.name}: {e}")

    def get_email(self, message_id: str) -> dict[str, Any]:
        path = self._index.get(message_id)
        if not path:
            raise EmailSourceError(
                f"Message {message_id} not found in {self.samples_dir}. "
                f"Known IDs: {list(self._index.keys())[:3]}..."
            )
        with open(path) as f:
            return json.load(f)

    def list_attachments(self, message_id: str) -> list[dict[str, Any]]:
        # JSON samples don't include attachment content; return empty list
        # if the email reports no attachments, else raise to signal a gap.
        email = self.get_email(message_id)
        if not email.get("hasAttachments"):
            return []
        # Has attachments per metadata, but file-based source can't fetch them
        raise EmailSourceError(
            f"Message {message_id} has attachments per metadata, but FileBasedEmailSource "
            f"does not include attachment content. Use APIBasedEmailSource for this email."
        )

    def fetch_attachment(self, message_id: str, attachment_id: str) -> bytes:
        raise EmailSourceError(
            "FileBasedEmailSource does not support attachment content. "
            "Use APIBasedEmailSource for attachment work."
        )

    def list_known_message_ids(self) -> list[str]:
        return sorted(self._index.keys())


# ============================================================
# API-based source — used in production
# ============================================================

class APIBasedEmailSource:
    """Fetches emails from the live email API (Microsoft Graph passthrough).

    Auth: x-api-key header.
    Response envelope: {"status": bool, "data": {...}, "error": null|str}.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.endpoint = endpoint or os.getenv("EMAIL_API_ENDPOINT")
        self.api_key = api_key or os.getenv("EMAIL_API_KEY")
        self.timeout = timeout or int(os.getenv("EMAIL_API_TIMEOUT", "60"))

        if not self.endpoint:
            raise EmailSourceError("EMAIL_API_ENDPOINT not configured")
        if not self.api_key:
            raise EmailSourceError("EMAIL_API_KEY not configured")

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Accept": "*/*",
        }

    def _unwrap(self, response: requests.Response) -> Any:
        """Validate response and extract the `data` field from the envelope."""
        if response.status_code != 200:
            raise EmailSourceError(
                f"HTTP {response.status_code}: {response.text[:300]}"
            )
        try:
            payload = response.json()
        except ValueError:
            raise EmailSourceError(f"Non-JSON response: {response.text[:200]}")

        if not isinstance(payload, dict):
            raise EmailSourceError(f"Unexpected response shape: {payload}")

        if payload.get("status") is False or payload.get("error"):
            raise EmailSourceError(
                f"API error: {payload.get('error') or payload.get('message')}"
            )

        return payload.get("data")

    def get_email(self, message_id: str) -> dict[str, Any]:
        url = f"{self.endpoint}/{message_id}"
        response = requests.get(url, headers=self._headers(), timeout=self.timeout)
        data = self._unwrap(response)
        if not isinstance(data, dict):
            raise EmailSourceError(f"Expected email dict, got {type(data)}")
        return data

    def list_attachments(self, message_id: str) -> list[dict[str, Any]]:
        url = f"{self.endpoint}/{message_id}/attachments"
        response = requests.get(url, headers=self._headers(), timeout=self.timeout)
        data = self._unwrap(response)
        if data is None:
            return []
        if not isinstance(data, list):
            raise EmailSourceError(f"Expected attachment list, got {type(data)}")
        return data

    def fetch_attachment(self, message_id: str, attachment_id: str) -> bytes:
        url = f"{self.endpoint}/{message_id}/attachments/{attachment_id}"
        response = requests.get(url, headers=self._headers(), timeout=self.timeout)
        # Attachment fetch may return either:
        # - JSON envelope with base64 content (Microsoft Graph style: data.contentBytes)
        # - Binary stream directly
        # Detect which and handle both.
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            data = self._unwrap(response)
            if isinstance(data, dict) and "contentBytes" in data:
                import base64
                return base64.b64decode(data["contentBytes"])
            raise EmailSourceError(
                f"JSON attachment response missing 'contentBytes': {data}"
            )
        # Binary stream
        if response.status_code != 200:
            raise EmailSourceError(
                f"HTTP {response.status_code}: {response.text[:300]}"
            )
        return response.content

    def list_known_message_ids(self) -> list[str]:
        # Live API doesn't have a "list all" — we'd need a separate
        # search/filter endpoint. For now, raise to signal this isn't
        # supported. In production, message IDs come from a queue.
        raise EmailSourceError(
            "APIBasedEmailSource does not support listing all message IDs. "
            "In production, message IDs come from an upstream queue or "
            "search endpoint. For dev, use FileBasedEmailSource."
        )


# ============================================================
# Factory — picks the right source based on env config
# ============================================================

def get_email_source() -> EmailSource:
    """Return the configured email source.

    Set EMAIL_SOURCE=file (default) for FileBasedEmailSource,
    or EMAIL_SOURCE=api for APIBasedEmailSource.
    """
    source_kind = os.getenv("EMAIL_SOURCE", "file").lower()
    if source_kind == "file":
        samples_dir = os.getenv("EMAIL_SAMPLES_DIR", "data/sample_emails_REAL")
        return FileBasedEmailSource(samples_dir)
    elif source_kind == "api":
        return APIBasedEmailSource()
    else:
        raise EmailSourceError(
            f"Unknown EMAIL_SOURCE={source_kind!r}. Use 'file' or 'api'."
        )
