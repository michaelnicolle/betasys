"""A small local invoice generator.

Run it with `python app.py` (or run.bat on Windows); it serves a web UI on
http://127.0.0.1:5000 and writes PDFs laid out like the Xero originals.
"""

import io
import json
import os
import re
import webbrowser
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, url_for)

import db
import pdfgen

app = Flask(__name__)
app.secret_key = "local-invoicer"
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # generous for a logo upload

ALLOWED_LOGO = {".png", ".jpg", ".jpeg", ".gif"}


# --- helpers -------------------------------------------------------------
def parse_date(value, fallback=None):
    """Accept the HTML date input format, falling back on bad input."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback or date.today()


def display_date(value):
    """Format as Xero does: '3 Jun 2026' (no leading zero on the day)."""
    d = parse_date(value)
    return f"{d.day} {d.strftime('%b')} {d.year}"


def decimal_or(value, default="0"):
    try:
        return Decimal(str(value).replace(",", "").strip() or default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def invoice_totals(items, gst_rate, amount_paid):
    rate = decimal_or(gst_rate)
    amounts = [pdfgen.q2(decimal_or(i["quantity"]) * decimal_or(i["unit_price"]))
               for i in items]
    subtotal = pdfgen.q2(sum(amounts, Decimal("0")))
    # Matches pdfgen: GST rounds per line, then sums.
    gst = sum((pdfgen.q2(a * rate / Decimal("100")) for a in amounts), Decimal("0"))
    total = pdfgen.q2(subtotal + gst)
    paid = pdfgen.q2(decimal_or(amount_paid))
    return {
        "subtotal": subtotal,
        "gst": gst,
        "total": total,
        "paid": paid,
        "due": pdfgen.q2(total - paid),
    }


def next_invoice_number(conn, settings, issue_date):
    """Fill the settings template, e.g. INV-{YYMMDD}-{SEQ:03}.

    {SEQ} counts existing invoices that share the same rendered prefix, so
    numbering restarts naturally when the date part rolls over.
    """
    template = settings.get("number_format") or "INV-{YYMMDD}-{SEQ:03}"
    tokens = {
        "YYYY": issue_date.strftime("%Y"),
        "YY": issue_date.strftime("%y"),
        "MM": issue_date.strftime("%m"),
        "DD": issue_date.strftime("%d"),
        "YYMMDD": issue_date.strftime("%y%m%d"),
        "YYYYMMDD": issue_date.strftime("%Y%m%d"),
        "YYYYMM": issue_date.strftime("%Y%m"),
    }
    rendered = template
    for key, value in tokens.items():
        rendered = rendered.replace("{" + key + "}", value)

    match = re.search(r"\{SEQ(?::0?(\d+))?\}", rendered)
    if not match:
        return rendered

    width = int(match.group(1) or 1)
    prefix = rendered[:match.start()]
    suffix = rendered[match.end():]
    pattern = re.escape(prefix) + r"(\d+)" + re.escape(suffix)
    highest = 0
    for row in conn.execute("SELECT number FROM invoices"):
        found = re.fullmatch(pattern, row["number"] or "")
        if found:
            highest = max(highest, int(found.group(1)))
    return f"{prefix}{highest + 1:0{width}d}{suffix}"


def items_from_form(form):
    """Read the repeating line-item fields the editor posts back."""
    items = []
    indexes = sorted({
        int(m.group(1))
        for key in form
        for m in [re.fullmatch(r"items\[(\d+)\]\[\w+\]", key)]
        if m
    })
    for index in indexes:
        def field(name):
            return form.get(f"items[{index}][{name}]", "").strip()

        code, name, description = field("code"), field("name"), field("description")
        quantity, unit_price = field("quantity"), field("unit_price")
        if not any([code, name, description]) and not decimal_or(quantity):
            continue  # a blank row the user never filled in
        items.append({
            "code": code,
            "name": name,
            "description": description,
            "quantity": str(decimal_or(quantity, "0")),
            "unit_price": str(decimal_or(unit_price, "0")),
        })
    return items


def build_render_payload(invoice):
    """Add the display-formatted fields the PDF renderer expects."""
    payload = dict(invoice)
    payload["issue_date_display"] = display_date(invoice["issue_date"])
    payload["due_date_display"] = display_date(invoice["due_date"])
    return payload


@app.context_processor
def inject_globals():
    conn = db.connect()
    settings = db.get_settings(conn)
    conn.close()
    return {"settings": settings, "today": date.today().isoformat()}


# --- invoices ------------------------------------------------------------
@app.route("/")
def index():
    conn = db.connect()
    rows = conn.execute(
        "SELECT i.*, c.name AS client_name FROM invoices i "
        "LEFT JOIN clients c ON c.id = i.client_id "
        "ORDER BY date(i.issue_date) DESC, i.id DESC"
    ).fetchall()
    invoices = []
    for row in rows:
        data = dict(row)
        data["client"] = json.loads(row["client_json"] or "{}")
        items = [
            dict(r) for r in conn.execute(
                "SELECT quantity, unit_price FROM line_items WHERE invoice_id = ?",
                (row["id"],))
        ]
        data["totals"] = invoice_totals(items, row["gst_rate"], row["amount_paid"])
        data["issue_display"] = display_date(row["issue_date"])
        invoices.append(data)
    conn.close()
    return render_template("invoices.html", invoices=invoices)


@app.route("/invoices/new")
def new_invoice():
    conn = db.connect()
    settings = db.get_settings(conn)
    clients = conn.execute(
        "SELECT * FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()
    issue = date.today()
    terms = int(decimal_or(settings.get("payment_terms_days"), "20"))
    invoice = {
        "id": None,
        "number": next_invoice_number(conn, settings, issue),
        "client_id": None,
        "issue_date": issue.isoformat(),
        "due_date": (issue + timedelta(days=terms)).isoformat(),
        "reference": "",
        "gst_rate": settings.get("gst_rate", "15"),
        "amount_paid": "0",
        "status": "draft",
        "footer_text": "",
        "items": [],
    }
    conn.close()
    return render_template("invoice_form.html", invoice=invoice, clients=clients)


@app.route("/invoices/<int:invoice_id>")
def edit_invoice(invoice_id):
    conn = db.connect()
    invoice = db.load_invoice(conn, invoice_id)
    if invoice is None:
        conn.close()
        abort(404)
    clients = conn.execute(
        "SELECT * FROM clients WHERE archived = 0 OR id = ? ORDER BY name",
        (invoice["client_id"] or -1,)
    ).fetchall()
    conn.close()
    return render_template("invoice_form.html", invoice=invoice, clients=clients)


@app.route("/invoices/save", methods=["POST"])
@app.route("/invoices/<int:invoice_id>/save", methods=["POST"])
def save_invoice(invoice_id=None):
    form = request.form
    conn = db.connect()
    settings = db.get_settings(conn)

    client_id = form.get("client_id") or None
    client_row = None
    if client_id:
        client_row = conn.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
    snapshot = db.client_snapshot(client_row)

    issue = parse_date(form.get("issue_date"))
    terms = int(decimal_or(settings.get("payment_terms_days"), "20"))
    due = parse_date(form.get("due_date"), issue + timedelta(days=terms))

    number = (form.get("number") or "").strip()
    if not number:
        number = next_invoice_number(conn, settings, issue)

    values = (
        number,
        client_row["id"] if client_row else None,
        json.dumps(snapshot),
        issue.isoformat(),
        due.isoformat(),
        (form.get("reference") or "").strip(),
        str(decimal_or(form.get("gst_rate"), settings.get("gst_rate", "15"))),
        str(decimal_or(form.get("amount_paid"))),
        form.get("status", "draft"),
        (form.get("footer_text") or "").strip(),
    )

    with conn:
        if invoice_id:
            conn.execute(
                "UPDATE invoices SET number=?, client_id=?, client_json=?, "
                "issue_date=?, due_date=?, reference=?, gst_rate=?, "
                "amount_paid=?, status=?, footer_text=? WHERE id=?",
                values + (invoice_id,),
            )
            conn.execute("DELETE FROM line_items WHERE invoice_id = ?", (invoice_id,))
        else:
            cursor = conn.execute(
                "INSERT INTO invoices (number, client_id, client_json, issue_date, "
                "due_date, reference, gst_rate, amount_paid, status, footer_text) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                values,
            )
            invoice_id = cursor.lastrowid

        for position, item in enumerate(items_from_form(form)):
            conn.execute(
                "INSERT INTO line_items (invoice_id, position, code, name, "
                "description, quantity, unit_price) VALUES (?,?,?,?,?,?,?)",
                (invoice_id, position, item["code"], item["name"],
                 item["description"], item["quantity"], item["unit_price"]),
            )
    conn.close()

    if form.get("action") == "pdf":
        return redirect(url_for("invoice_pdf", invoice_id=invoice_id))
    flash(f"Saved invoice {number}.", "ok")
    return redirect(url_for("edit_invoice", invoice_id=invoice_id))


@app.route("/invoices/<int:invoice_id>/pdf")
def invoice_pdf(invoice_id):
    conn = db.connect()
    invoice = db.load_invoice(conn, invoice_id)
    settings = db.get_settings(conn)
    conn.close()
    if invoice is None:
        abort(404)

    buffer = io.BytesIO()
    pdfgen.build_invoice_pdf(buffer, build_render_payload(invoice), settings)
    buffer.seek(0)
    safe_number = re.sub(r"[^A-Za-z0-9._-]", "_", invoice["number"] or "invoice")
    inline = request.args.get("inline") == "1"
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=not inline,
        download_name=f"Invoice_{safe_number}.pdf",
    )


@app.route("/invoices/<int:invoice_id>/duplicate", methods=["POST"])
def duplicate_invoice(invoice_id):
    conn = db.connect()
    invoice = db.load_invoice(conn, invoice_id)
    settings = db.get_settings(conn)
    if invoice is None:
        conn.close()
        abort(404)

    issue = date.today()
    terms = int(decimal_or(settings.get("payment_terms_days"), "20"))
    with conn:
        cursor = conn.execute(
            "INSERT INTO invoices (number, client_id, client_json, issue_date, "
            "due_date, reference, gst_rate, amount_paid, status, footer_text) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (next_invoice_number(conn, settings, issue), invoice["client_id"],
             json.dumps(invoice["client"]), issue.isoformat(),
             (issue + timedelta(days=terms)).isoformat(), invoice["reference"],
             invoice["gst_rate"], "0", "draft", invoice["footer_text"]),
        )
        new_id = cursor.lastrowid
        for position, item in enumerate(invoice["items"]):
            conn.execute(
                "INSERT INTO line_items (invoice_id, position, code, name, "
                "description, quantity, unit_price) VALUES (?,?,?,?,?,?,?)",
                (new_id, position, item["code"], item["name"],
                 item["description"], item["quantity"], item["unit_price"]),
            )
    conn.close()
    flash("Copied to a new draft invoice.", "ok")
    return redirect(url_for("edit_invoice", invoice_id=new_id))


@app.route("/invoices/<int:invoice_id>/delete", methods=["POST"])
def delete_invoice(invoice_id):
    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM line_items WHERE invoice_id = ?", (invoice_id,))
        conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    conn.close()
    flash("Invoice deleted.", "ok")
    return redirect(url_for("index"))


# --- clients -------------------------------------------------------------
@app.route("/clients")
def clients():
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM clients WHERE archived = 0 ORDER BY name"
    ).fetchall()
    conn.close()
    return render_template("clients.html", clients=rows)


@app.route("/clients/save", methods=["POST"])
def save_client():
    form = request.form
    client_id = form.get("id")
    values = (
        (form.get("name") or "").strip(),
        (form.get("address") or "").strip(),
        (form.get("email") or "").strip(),
        (form.get("phone") or "").strip(),
    )
    if not values[0]:
        flash("A client needs a name.", "error")
        return redirect(url_for("clients"))

    conn = db.connect()
    with conn:
        if client_id:
            conn.execute(
                "UPDATE clients SET name=?, address=?, email=?, phone=? WHERE id=?",
                values + (client_id,),
            )
        else:
            conn.execute(
                "INSERT INTO clients (name, address, email, phone) VALUES (?,?,?,?)",
                values,
            )
    conn.close()
    flash("Client saved.", "ok")
    return redirect(url_for("clients"))


@app.route("/clients/<int:client_id>/delete", methods=["POST"])
def delete_client(client_id):
    conn = db.connect()
    with conn:
        # Archive rather than delete, so old invoices keep their link.
        conn.execute("UPDATE clients SET archived = 1 WHERE id = ?", (client_id,))
    conn.close()
    flash("Client removed.", "ok")
    return redirect(url_for("clients"))


# --- settings ------------------------------------------------------------
@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    conn = db.connect()
    if request.method == "POST":
        keys = ["company_name", "company_address", "gst_number", "title",
                "currency_symbol", "gst_rate", "gst_label", "payment_terms_days",
                "number_format", "footer_text", "accent_color"]
        db.save_settings(conn, {k: request.form.get(k, "") for k in keys})

        upload = request.files.get("logo")
        if upload and upload.filename:
            extension = os.path.splitext(upload.filename)[1].lower()
            if extension not in ALLOWED_LOGO:
                flash("Logo must be a PNG, JPG or GIF.", "error")
            else:
                upload.save(os.path.join(pdfgen.ASSETS, "logo.png"))
        conn.close()
        flash("Settings saved.", "ok")
        return redirect(url_for("settings_page"))

    current = db.get_settings(conn)
    conn.close()
    return render_template("settings.html", current=current)


@app.route("/sample.pdf")
def sample_pdf():
    """Preview the current branding without saving anything."""
    conn = db.connect()
    settings = db.get_settings(conn)
    conn.close()
    today = date.today()
    invoice = {
        "number": "INV-SAMPLE-001",
        "reference": "Sample",
        "issue_date_display": display_date(today.isoformat()),
        "due_date_display": display_date((today + timedelta(days=20)).isoformat()),
        "gst_rate": settings.get("gst_rate", "15"),
        "amount_paid": "0",
        "footer_text": "",
        "client": {
            "name": "Sample Client Ltd",
            "address": "1 Example Street\nSomewhere\nAuckland 1010\nNew Zealand",
            "email": "accounts@example.co.nz",
            "phone": "",
        },
        "items": [{
            "code": "PS", "name": "Professional Services",
            "description": "Sample engagement\nEffort to date",
            "quantity": "10", "unit_price": "150.00",
        }],
    }
    buffer = io.BytesIO()
    pdfgen.build_invoice_pdf(buffer, invoice, settings)
    buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf",
                     as_attachment=False, download_name="sample.pdf")


if __name__ == "__main__":
    db.init()
    port = int(os.environ.get("PORT", "5000"))
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        webbrowser.open(f"http://127.0.0.1:{port}/")
    app.run(host="127.0.0.1", port=port, debug=False)
