"""SQLite storage for the invoicer app.

Everything lives in a single file (invoices.db) next to the app, so the whole
thing can be copied to a USB stick or backed up by dragging one file.
"""

import json
import os
import sqlite3
from decimal import Decimal

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "invoices.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    address  TEXT NOT NULL DEFAULT '',
    email    TEXT NOT NULL DEFAULT '',
    phone    TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS invoices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    number        TEXT NOT NULL,
    client_id     INTEGER,
    client_json   TEXT NOT NULL DEFAULT '{}',
    issue_date    TEXT NOT NULL,
    due_date      TEXT NOT NULL,
    reference     TEXT NOT NULL DEFAULT '',
    gst_rate      TEXT NOT NULL DEFAULT '15',
    amount_paid   TEXT NOT NULL DEFAULT '0',
    status        TEXT NOT NULL DEFAULT 'draft',
    footer_text   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS line_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0,
    code        TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    quantity    TEXT NOT NULL DEFAULT '1',
    unit_price  TEXT NOT NULL DEFAULT '0',
    FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_items_invoice ON line_items (invoice_id, position);
"""

# Seeded from the existing Xero invoices so the app is usable immediately.
DEFAULT_SETTINGS = {
    "company_name": "Beta Business Systems Ltd",
    "company_address": "254 Sunset Road Windsor Park\nAuckland 0632\nNew Zealand",
    "gst_number": "071-443-760",
    "title": "Tax Invoice",
    "currency_symbol": "$",
    "gst_rate": "15",
    "gst_label": "Total GST",
    "payment_terms_days": "20",
    "number_format": "INV-{YYMMDD}-{SEQ:03}",
    "footer_text": (
        "Thankyou for your continued business\n"
        "Direct Credit Payments to: BNZ Bank, Account Number: "
        "02-1244-0191485-000\n"
        "(Please ensure you reference your company name and the invoice "
        "number in the payment details)"
    ),
    "accent_color": "#22C55E",
}


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init():
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
    conn.close()


def get_settings(conn):
    settings = dict(DEFAULT_SETTINGS)
    for row in conn.execute("SELECT key, value FROM settings"):
        settings[row["key"]] = row["value"]
    return settings


def save_settings(conn, values):
    with conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )


def client_snapshot(row):
    """Freeze the client's details onto the invoice.

    A past invoice should keep showing the address it was actually sent to,
    even after the client record is edited.
    """
    if row is None:
        return {"name": "", "address": "", "email": "", "phone": ""}
    return {
        "name": row["name"],
        "address": row["address"],
        "email": row["email"],
        "phone": row["phone"],
    }


def load_invoice(conn, invoice_id):
    invoice = conn.execute(
        "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
    ).fetchone()
    if invoice is None:
        return None
    data = dict(invoice)
    data["client"] = json.loads(data.pop("client_json") or "{}")
    data["items"] = [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM line_items WHERE invoice_id = ? ORDER BY position, id",
            (invoice_id,),
        )
    ]
    return data


def money(value):
    """Parse anything the form might send into a Decimal, defaulting to 0."""
    try:
        return Decimal(str(value).replace(",", "").strip() or "0")
    except Exception:
        return Decimal("0")
