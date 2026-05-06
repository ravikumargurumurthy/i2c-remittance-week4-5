# smoke_test.py
"""
End-to-end smoke test of the Project 1 foundation.

For each of the 10 real sample emails:
1. Load via email_source
2. Extract HTML tables
3. Print a structured summary

Catches structural surprises before we build the agent on top.
"""

from email_source import get_email_source
from html_tools import extract_html_tables, extract_plain_text


def main():
    src = get_email_source()
    ids = src.list_known_message_ids()
    print(f"Found {len(ids)} sample emails")
    print("=" * 80)

    for i, mid in enumerate(ids, 1):
        email = src.get_email(mid)
        subject = email.get("subject", "")
        sender = email.get("sender", {}).get("emailAddress", {}).get("address", "")
        has_attach = email.get("hasAttachments", False)
        body_html = email.get("body", {}).get("content", "")

        text = extract_plain_text(body_html)
        tables = extract_html_tables(body_html)

        print(f"\n[{i}] {subject}")
        print(f"    From: {sender}")
        print(f"    Attachments: {'yes' if has_attach else 'no'}")
        print(f"    Body: {len(body_html)} chars HTML, {len(text)} chars text")
        print(f"    Tables: {len(tables)}")
        for j, tbl in enumerate(tables):
            header_str = (
                ", ".join(tbl["header_row"][:6]) + ("..." if len(tbl["header_row"] or []) > 6 else "")
                if tbl["header_row"]
                else "(no header detected)"
            )
            print(f"      Table {j}: {tbl['row_count']}×{tbl['col_count']} | {header_str}")

    print("\n" + "=" * 80)
    print("Smoke test complete.")


if __name__ == "__main__":
    main()
