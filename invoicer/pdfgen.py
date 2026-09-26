"""Renders an invoice as a PDF laid out to match the Xero template.

Every measurement here was taken off the original Xero PDFs: page size,
margins, baselines, leading, rule thickness and colours all match, so a new
invoice drops straight into a run of old ones without looking different.

Coordinates in this module are top-down (y grows downward, like the Xero
source) and are flipped once, in Canvas.text/line helpers, to ReportLab's
bottom-up space.
"""

import os
import re
from decimal import Decimal, ROUND_HALF_UP

from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(APP_DIR, "assets")

FONT_REGULAR = "Inter"
FONT_BOLD = "Inter-Bold"

# --- page geometry -------------------------------------------------------
PAGE_W, PAGE_H = 595.92, 842.88
LEFT, RIGHT = 30.0, 566.25
PAD = 6.0                     # cell padding inside table columns
GRAY = (0.7725, 0.7765, 0.7843)
BLACK = (0, 0, 0)
LINK_BLUE = (0.0, 0.4706, 0.7843)
ITALIC_SKEW = 0.25          # faux italic, as used on the 'Less amount paid' row

# Table column boundaries (Item | Description | Quantity | Price | Amount).
COLS = [30.0, 90.0, 386.25, 446.25, 506.25, 566.25]
RULE = 0.75                   # hairline thickness used for row separators
ACCENT_RULE = 1.5             # thicker rule under the header block
BOTTOM_BAR = 4.5              # full-bleed accent bar at the foot of the page

BODY = 9.0
LINE = 13.5                   # leading for address/footer paragraphs
BLOCK_GAP = 19.5              # gap between address and contact blocks
WRAP_LEAD = 12.0              # leading between wrapped lines of one paragraph
PARA_LEAD = 12.75             # leading between explicitly separate lines
ROW_TOP_GAP = 14.25           # separator -> first baseline of a row
ROW_BOTTOM_GAP = 8.25         # last baseline of a row -> next separator
MAX_Y = 800.25                # lowest a row separator may be placed
CONT_HEADER_Y = 43.5          # table header baseline on continuation pages

FOOTER_WRAP = 260.0           # wrap width for the payment/thankyou block
TOTALS_LABEL_X = 423.0
TOTALS_SPLIT = 509.25         # column split inside the totals mini-table
TOTALS_LEFT = 422.25

_fonts_registered = False


def _register_fonts():
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, os.path.join(ASSETS, "Inter-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, os.path.join(ASSETS, "Inter-Bold.ttf")))
    pdfmetrics.registerFontFamily(FONT_REGULAR, normal=FONT_REGULAR, bold=FONT_BOLD)
    _fonts_registered = True


def hex_to_rgb(value, fallback=(0.1333, 0.7725, 0.3686)):
    value = (value or "").strip().lstrip("#")
    if len(value) != 6:
        return fallback
    try:
        return tuple(int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return fallback


def q2(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_amount(value):
    """2dp with thousands separators, as Xero prints line and total amounts."""
    return f"{q2(value):,.2f}"


def fmt_qty(value):
    """Quantities print without trailing zeros: 1, 5.5, 14."""
    d = Decimal(value)
    if d == d.to_integral_value():
        return str(d.to_integral_value())
    return str(d.normalize())


class Layout:
    """A thin wrapper over the ReportLab canvas that thinks top-down."""

    def __init__(self, canvas):
        self.c = canvas

    def flip(self, y):
        return PAGE_H - y

    def text(self, x, baseline, value, size=BODY, bold=False, color=BLACK,
             skew=0.0):
        if value == "":
            return
        font = FONT_BOLD if bold else FONT_REGULAR
        self.c.setFillColorRGB(*color)
        if skew:
            obj = self.c.beginText()
            obj.setTextTransform(1, 0, skew, 1, x, self.flip(baseline))
            obj.setFont(font, size)
            obj.textOut(value)
            self.c.drawText(obj)
            return
        self.c.setFont(font, size)
        self.c.drawString(x, self.flip(baseline), value)

    def text_right(self, x_right, baseline, value, size=BODY, bold=False, color=BLACK):
        if value == "":
            return
        self.c.setFont(FONT_BOLD if bold else FONT_REGULAR, size)
        self.c.setFillColorRGB(*color)
        self.c.drawRightString(x_right, self.flip(baseline), value)

    def rule(self, x0, y_top, x1, thickness, color=GRAY):
        self.c.setFillColorRGB(*color)
        self.c.rect(x0, self.flip(y_top + thickness), x1 - x0, thickness,
                    stroke=0, fill=1)


def width_of(value, size, bold=False):
    return pdfmetrics.stringWidth(value, FONT_BOLD if bold else FONT_REGULAR, size)


def wrap(value, max_width, size=BODY, bold=False):
    """Greedy word wrap that may also break after a hyphen, as Xero does."""
    if not value:
        return []
    lines, current = [], ""
    for word in value.split():
        for index, part in enumerate(re.split(r"(?<=-)", word)):
            if not part:
                continue
            separator = " " if (current and index == 0) else ""
            candidate = current + separator + part
            if current and width_of(candidate, size, bold) > max_width:
                lines.append(current)
                current = part
            else:
                current = candidate
            # A fragment wider than the whole column still has to be split.
            while width_of(current, size, bold) > max_width and len(current) > 1:
                cut = len(current)
                while cut > 1 and width_of(current[:cut], size, bold) > max_width:
                    cut -= 1
                lines.append(current[:cut])
                current = current[cut:]
    if current:
        lines.append(current)
    return lines


def description_lines(item, max_width):
    """Flatten an item's name + description into (text, leading) pairs.

    Wrapped continuation lines sit tighter than lines the user typed as
    separate paragraphs; the leading on the very first line is never used.
    """
    out = []
    name = (item.get("name") or "").strip()
    if name:
        for index, line in enumerate(wrap(name, max_width)):
            out.append((line, WRAP_LEAD))
    body = (item.get("description") or "").replace("\r\n", "\n")
    paragraphs = [p.strip() for p in body.split("\n") if p.strip()]
    for para_index, paragraph in enumerate(paragraphs):
        for index, line in enumerate(wrap(paragraph, max_width)):
            if index > 0:
                lead = WRAP_LEAD          # wrapped continuation
            elif para_index == 0:
                lead = WRAP_LEAD          # first paragraph follows the name
            else:
                lead = PARA_LEAD          # a line the user typed separately
            out.append((line, lead))
    if not out:
        out.append(("", WRAP_LEAD))
    return out


def snap(value, grid=0.75):
    """Xero's renderer lands on a 0.75pt grid; matching it avoids drift."""
    return (int(value / grid)) * grid


def build_invoice_pdf(stream, invoice, settings):
    _register_fonts()
    accent = hex_to_rgb(settings.get("accent_color"))
    c = rl_canvas.Canvas(stream, pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Invoice {invoice['number']}")
    c.setAuthor(settings.get("company_name", ""))
    L = Layout(c)

    doc = _Document(c, L, invoice, settings, accent)
    doc.render()
    c.save()


class _Document:
    def __init__(self, c, layout, invoice, settings, accent):
        self.c = c
        self.L = layout
        self.inv = invoice
        self.s = settings
        self.accent = accent
        self.currency = settings.get("currency_symbol", "$") or "$"

        rate = Decimal(str(invoice.get("gst_rate") or "0"))
        amounts = [q2(Decimal(i["quantity"]) * Decimal(i["unit_price"]))
                   for i in invoice["items"]]
        self.gst_rate = rate
        self.subtotal = q2(sum(amounts, Decimal("0")))
        # GST is rounded per line and then summed - rounding the subtotal
        # instead drifts a cent or two away from the Xero originals.
        self.gst = sum((q2(a * rate / Decimal("100")) for a in amounts), Decimal("0"))
        self.total = q2(self.subtotal + self.gst)
        self.paid = q2(Decimal(str(invoice.get("amount_paid") or "0")))
        self.due = q2(self.total - self.paid)

    # -- page furniture ---------------------------------------------------
    def _accent_bar(self):
        self.c.setFillColorRGB(*self.accent)
        self.c.rect(0, 0, PAGE_W, BOTTOM_BAR, stroke=0, fill=1)

    def _new_page(self):
        self._accent_bar()
        self.c.showPage()
        self._accent_bar()

    def _table_header(self, baseline):
        L = self.L
        L.text(COLS[0] + PAD, baseline, "Item")
        L.text(COLS[1] + PAD, baseline, "Description")
        L.text_right(COLS[3] - PAD, baseline, "Quantity")
        L.text_right(COLS[4] - PAD, baseline, "Price")
        L.text_right(COLS[5] - PAD, baseline, "Amount")
        sep = baseline + ROW_BOTTOM_GAP
        L.rule(COLS[0], sep, COLS[5], RULE)
        return sep

    # -- page 1 header ----------------------------------------------------
    def _header(self):
        L = self.L
        L.text(LEFT, 45.0, self.s.get("title", "Tax Invoice") or "Tax Invoice",
               size=18.0, bold=True)
        self._logo()

        client = self.inv.get("client") or {}
        y = 90.0
        L.text(LEFT, y, "Bill to", bold=True)
        y += LINE
        # Name first, then the address - mirroring the company block opposite.
        client_lines = [l for l in [(client.get("name") or "").strip()] +
                        (client.get("address") or "").splitlines() if l.strip()]
        for line in client_lines:
            L.text(LEFT, y, line.strip())
            y += LINE
        contacts = [v for v in (client.get("email"), client.get("phone")) if (v or "").strip()]
        if contacts:
            y += BLOCK_GAP - LINE
            for value in contacts:
                L.text(LEFT, y, value.strip())
                y += LINE
        client_bottom = y - LINE

        y = 90.0
        for line in [self.s.get("company_name", "")] + [
            l for l in (self.s.get("company_address") or "").splitlines() if l.strip()
        ]:
            L.text_right(RIGHT, y, line.strip())
            y += LINE
        gst_number = (self.s.get("gst_number") or "").strip()
        if gst_number:
            y += BLOCK_GAP - LINE
            L.text_right(RIGHT, y, f"GST: {gst_number}")
            y += LINE
        company_bottom = y - LINE

        return max(client_bottom, company_bottom) + 31.5

    def _logo(self):
        path = os.path.join(ASSETS, "logo.png")
        if not os.path.exists(path):
            return
        try:
            image = ImageReader(path)
            iw, ih = image.getSize()
        except Exception:
            return
        max_w, max_h = 162.75, 32.25
        scale = min(max_w / iw, max_h / ih)
        w, h = iw * scale, ih * scale
        # Right-aligned in the header, top edge at y=36.
        self.c.drawImage(image, RIGHT - w, self.L.flip(36.0 + h), w, h,
                         mask="auto", preserveAspectRatio=True)

    def _summary(self, label_y):
        """The Amount due / Due date / Issue date / Number / Reference strip."""
        L = self.L
        fields = [
            ("Amount due", f"{self.currency}{fmt_amount(self.due)}", 13.5),
            ("Due date", self.inv.get("due_date_display", ""), 13.5),
            ("Issue date", self.inv.get("issue_date_display", ""), 10.5),
            ("Invoice number", self.inv.get("number", ""), 10.5),
            ("Reference", self.inv.get("reference", ""), 10.5),
        ]
        value_y = label_y + 18.0
        x = LEFT
        for label, value, size in fields:
            if not str(value).strip():
                continue
            L.text(x, label_y, label)
            L.text(x, value_y, str(value), size=size, bold=True)
            x += max(width_of(label, BODY), width_of(str(value), size, True)) + 24.0
        return label_y

    # -- line item table --------------------------------------------------
    def _flatten_rows(self):
        desc_width = COLS[2] - COLS[1] - 2 * PAD
        rows = []
        for item in self.inv["items"]:
            amount = q2(Decimal(item["quantity"]) * Decimal(item["unit_price"]))
            rows.append({
                "code": (item.get("code") or "").strip(),
                "lines": description_lines(item, desc_width),
                "qty": fmt_qty(item["quantity"]),
                "price": fmt_amount(item["unit_price"]),
                "amount": fmt_amount(amount),
            })
        return rows

    def _draw_table(self, sep_y):
        """Lay out rows, breaking pages (and splitting descriptions) as needed."""
        L = self.L
        for row in self._flatten_rows():
            pending = list(row["lines"])
            numbers_drawn = False
            while pending:
                baselines = []
                y = sep_y
                consumed = 0
                for text, lead in pending:
                    next_y = y + (ROW_TOP_GAP if not baselines else lead)
                    if next_y + ROW_BOTTOM_GAP > MAX_Y and baselines:
                        break
                    y = next_y
                    baselines.append((y, text))
                    consumed += 1

                if not baselines:
                    # Nothing fits on this page at all - start a fresh one.
                    sep_y = self._continue_page()
                    continue

                for baseline, text in baselines:
                    L.text(COLS[1] + PAD, baseline, text)

                if not numbers_drawn:
                    mid = snap((baselines[0][0] + baselines[-1][0]) / 2.0)
                    L.text(COLS[0] + PAD, mid, row["code"])
                    L.text_right(COLS[3] - PAD, mid, row["qty"])
                    L.text_right(COLS[4] - PAD, mid, row["price"])
                    L.text_right(COLS[5] - PAD, mid, row["amount"])
                    numbers_drawn = True

                sep_y = baselines[-1][0] + ROW_BOTTOM_GAP
                L.rule(COLS[0], sep_y, COLS[5], RULE)
                pending = pending[consumed:]

                if pending:
                    sep_y = self._continue_page()

            if sep_y + ROW_TOP_GAP + ROW_BOTTOM_GAP > MAX_Y:
                sep_y = self._continue_page()
        return sep_y

    def _continue_page(self, with_header=True):
        """Start a new page; the table header only repeats above real rows."""
        self._new_page()
        if not with_header:
            return CONT_HEADER_Y - ROW_TOP_GAP
        return self._table_header(CONT_HEADER_Y)

    # -- totals and footer ------------------------------------------------
    def _totals(self, table_bottom):
        L = self.L
        rate_label = self.s.get("gst_label", "Total GST") or "Total GST"
        rate_text = fmt_qty(self.gst_rate)

        y = table_bottom + 20.25
        L.text(TOTALS_LABEL_X, y, "Subtotal")
        L.text_right(COLS[5] - PAD, y, fmt_amount(self.subtotal))

        if self.gst_rate != 0:
            y += 15.75
            L.text(TOTALS_LABEL_X, y, f"{rate_label} {rate_text}%")
            L.text_right(COLS[5] - PAD, y, fmt_amount(self.gst))

        sep = y + 11.25
        self._totals_rule(sep)

        y = sep + 17.25
        L.text(TOTALS_LABEL_X, y, "Total")
        L.text_right(COLS[5] - PAD, y, fmt_amount(self.total))

        if self.paid != 0:
            y += 15.75
            L.text(TOTALS_LABEL_X, y, "Less amount paid", skew=ITALIC_SKEW)
            L.text_right(COLS[5] - PAD, y, fmt_amount(self.paid))

        sep = y + 11.25
        self._totals_rule(sep)

        y = sep + 20.25
        L.text(TOTALS_LABEL_X, y, "Amount due")
        L.text_right(COLS[5] - PAD, y + 1.5,
                     f"{self.currency}{fmt_amount(self.due)}", size=13.5, bold=True)
        return y + 1.5

    def _totals_height(self):
        """How far the totals block reaches below the last table separator."""
        height = 20.25
        if self.gst_rate != 0:
            height += 15.75
        height += 11.25 + 17.25
        if self.paid != 0:
            height += 15.75
        return height + 11.25 + 20.25 + 1.5

    def _footer_height(self):
        text = self.inv.get("footer_text") or self.s.get("footer_text") or ""
        lines = 0
        for paragraph in text.replace("\r\n", "\n").split("\n"):
            lines += len(wrap(paragraph.rstrip(), FOOTER_WRAP)) or 1
        return lines * LINE

    def _totals_rule(self, y):
        self.L.rule(TOTALS_LEFT, y, TOTALS_SPLIT, RULE)
        self.L.rule(TOTALS_SPLIT, y, COLS[5], RULE)

    def _footer(self, first_baseline):
        text = self.inv.get("footer_text") or self.s.get("footer_text") or ""
        y = first_baseline
        for paragraph in text.replace("\r\n", "\n").split("\n"):
            paragraph = paragraph.rstrip()
            if not paragraph:
                y += LINE
                continue
            for line in wrap(paragraph, FOOTER_WRAP):
                self.L.text(LEFT, y, line)
                y += LINE
        return y

    # -- entry point ------------------------------------------------------
    def render(self):
        self._accent_bar()
        label_y = self._header()
        self._summary(label_y)

        rule_top = label_y + 61.5
        self.L.rule(LEFT, rule_top, RIGHT, ACCENT_RULE, color=self.accent)
        sep_y = self._table_header(rule_top + 24.0)
        table_bottom = self._draw_table(sep_y)

        # Keep the totals and the payment block together on one page.
        needed = self._totals_height() + 3.75 + self._footer_height()
        if table_bottom + needed > PAGE_H - 12.0:
            table_bottom = self._continue_page(with_header=False)

        totals_bottom = self._totals(table_bottom)
        left_bottom = table_bottom + 27.75 + LINE
        self._footer(max(left_bottom, totals_bottom + 3.75))
        self._accent_bar()
