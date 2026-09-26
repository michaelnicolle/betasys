# Invoicer

A small invoice generator that runs on your own laptop. It produces PDFs laid
out to match the Xero invoices you were sending previously — same page size,
fonts, spacing, rules and accent colour — so new invoices sit alongside the old
ones without looking different.

Nothing leaves your machine: it's a local web page backed by a single SQLite
file.

## Running it on Windows

1. Install Python from <https://www.python.org/downloads/>, ticking
   **"Add python.exe to PATH"** during setup. (Skip if you already have it.)
2. Double-click **`run.bat`**.

The first run takes a minute while it sets itself up, then your browser opens
at <http://127.0.0.1:5000>. After that, `run.bat` starts it in a couple of
seconds. Close the black console window to stop it.

### Running it by hand (macOS, Linux, or if you prefer a terminal)

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

## Using it

**Settings** — your business name, address, GST number, logo, footer text and
invoice-number format. These are pre-filled from your existing invoices, so
you can go straight to invoicing.

**Clients** — name, address, email and phone. The *Bill to* block prints the
client name first, then the address exactly as you type it, one line per
line, then the email and phone.

**Invoices** — pick a client, add lines, save. Each line has:

| Field | Prints as |
|---|---|
| Item | the short code in the first column (e.g. `PS`) |
| Name | an optional first line of the description |
| Description | the description body — press Enter for a new line |
| Quantity, Price | the numeric columns; Amount is calculated |

Totals update as you type. **Save & download PDF** writes the file to your
Downloads folder; **Preview PDF** opens it in a browser tab without saving.

Long invoices flow onto extra pages automatically, repeating the column
headers and splitting a long description across the page break, the same way
Xero did.

### Invoice numbers

The number is suggested from the format in Settings and stays editable. The
default is `INV-{YYMMDD}-{SEQ:03}`, giving `INV-260603-001`. Available tokens:

`{YYYY}` `{YY}` `{MM}` `{DD}` `{YYMMDD}` `{YYYYMMDD}` `{YYYYMM}` `{SEQ:03}`

`{SEQ}` counts up within the numbers that already share the same prefix, so it
restarts on its own when the date part changes. For a per-client number like
`INV-MFGM-20260731`, just type it in — whatever you type is kept.

### GST

Prices are GST-exclusive. GST is added at the rate on the invoice (defaulting
to the rate in Settings) and rounded to the cent. Set the rate to `0` on an
invoice to leave the GST line off entirely.

**Amount already paid** prints the *Less amount paid* line and reduces the
amount due — set it to the invoice total to produce a receipted invoice
showing `$0.00` due.

## Your data

Everything lives in **`invoices.db`** next to the app, and your logo in
`assets/logo.png`. To back up or move to another machine, copy the whole
folder. Deleting `invoices.db` starts over from scratch.

Editing a client changes future invoices only — each invoice keeps a copy of
the address it was actually sent to, so old PDFs never change.

## Notes

- The Xero *View online* QR code and link are deliberately left out; there is
  no hosted copy of these invoices to link to.
- The fonts in `assets/` are Inter (SIL Open Font License), the same typeface
  Xero's template uses.
