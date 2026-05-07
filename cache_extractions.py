# cache_extractions.py
"""
Run the agent against all sample emails and cache results to a JSON file.

Used by the Streamlit HITL UI to display extractions without running the
agent on every page load. Re-run this script to refresh the cache after
changing prompts, schemas, or sample data.

Usage:
    python cache_extractions.py
"""

import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from email_source import get_email_source
from agent import process_email


CACHE_PATH = Path("data/extractions_cache.json")


def serialize_for_json(obj):
    """Custom serializer for Decimal, datetime, and Pydantic models."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"Type {type(obj).__name__} not serializable")


def main():
    print("Cache extractions — running agent against all samples")
    print("=" * 70)

    src = get_email_source()
    message_ids = src.list_known_message_ids()

    cached = []
    failed = []

    for i, mid in enumerate(message_ids, 1):
        try:
            email = src.get_email(mid)
            subject = (email.get("subject") or "(no subject)")[:50]
            print(f"[{i}/{len(message_ids)}] {subject} ...", end=" ", flush=True)

            result = process_email(mid, email)

            if result.get("error"):
                print(f"ERROR: {result['error']}")
                failed.append({"message_id": mid, "subject": subject, "error": result["error"]})
                continue

            extraction = result.get("extraction")
            if not extraction:
                print("no extraction")
                failed.append({"message_id": mid, "subject": subject, "error": "no extraction"})
                continue

            # Build the cache entry
            entry = {
                "message_id": mid,
                "cached_at": datetime.utcnow().isoformat() + "Z",
                "email_metadata": {
                    "subject": email.get("subject"),
                    "sender": (email.get("sender") or {})
                              .get("emailAddress", {}).get("address"),
                    "received_at": email.get("receivedDateTime"),
                    "has_attachments": email.get("hasAttachments", False),
                },
                "email_body_html": email.get("body", {}).get("content", ""),
                "extraction": json.loads(json.dumps(
                    extraction.model_dump(mode="json"),
                    default=serialize_for_json,
                )),
            }
            cached.append(entry)
            print(f"OK ({extraction.routing_decision.value})")

        except Exception as e:
            print(f"EXCEPTION: {type(e).__name__}: {e}")
            failed.append({
                "message_id": mid,
                "subject": (email.get("subject") if email else "(unknown)"),
                "error": f"{type(e).__name__}: {e}",
            })

    # Write cache
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(
            {
                "cached_at": datetime.utcnow().isoformat() + "Z",
                "total": len(message_ids),
                "succeeded": len(cached),
                "failed": len(failed),
                "extractions": cached,
                "failures": failed,
            },
            f,
            indent=2,
            default=serialize_for_json,
        )

    print()
    print("=" * 70)
    print(f"Cached {len(cached)} extractions to {CACHE_PATH}")
    if failed:
        print(f"Failed: {len(failed)}")
        for f in failed:
            print(f"  - {f['subject']}: {f['error']}")
    print()


if __name__ == "__main__":
    main()
