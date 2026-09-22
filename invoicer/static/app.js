/* Line-item editor: repeating rows with live totals. */
(function () {
  var body = document.getElementById('items-body');
  if (!body) { return; }

  var template = document.getElementById('row-template');
  var FIELDS = ['code', 'name', 'description', 'quantity', 'unit_price'];

  function parseNumber(value) {
    var n = parseFloat(String(value).replace(/,/g, '').trim());
    return isFinite(n) ? n : 0;
  }

  function format(n) {
    return n.toLocaleString('en-NZ', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function autoGrow(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.max(textarea.scrollHeight, 44) + 'px';
  }

  function addRow(item) {
    var row = template.content.firstElementChild.cloneNode(true);
    FIELDS.forEach(function (field) {
      var input = row.querySelector('.f-' + field);
      if (item && item[field] !== undefined && item[field] !== null) {
        input.value = item[field];
      }
    });
    body.appendChild(row);
    autoGrow(row.querySelector('.f-description'));
    renumber();
    recalc();
    return row;
  }

  /* Names carry the row index so Flask can rebuild the list on POST. */
  function renumber() {
    Array.prototype.forEach.call(body.querySelectorAll('.item-row'), function (row, index) {
      FIELDS.forEach(function (field) {
        row.querySelector('.f-' + field).name = 'items[' + index + '][' + field + ']';
      });
    });
  }

  function recalc() {
    var rate = parseNumber(document.getElementById('gst-rate').value);
    var subtotal = 0;
    var gst = 0;
    Array.prototype.forEach.call(body.querySelectorAll('.item-row'), function (row) {
      var amount = parseNumber(row.querySelector('.f-quantity').value) *
                   parseNumber(row.querySelector('.f-unit_price').value);
      amount = Math.round(amount * 100) / 100;
      row.querySelector('.amount').textContent = format(amount);
      subtotal += amount;
      /* GST rounds per line then sums, the way Xero does it. */
      gst += Math.round(amount * rate) / 100;
    });
    subtotal = Math.round(subtotal * 100) / 100;
    gst = Math.round(gst * 100) / 100;
    var total = Math.round((subtotal + gst) * 100) / 100;
    var paid = parseNumber(document.getElementById('amount-paid').value);

    document.getElementById('t-subtotal').textContent = format(subtotal);
    document.getElementById('t-rate').textContent = rate;
    document.getElementById('t-gst').textContent = format(gst);
    document.getElementById('t-total').textContent = format(total);
    document.getElementById('t-paid').textContent = format(paid);
    document.getElementById('t-due').textContent = window.CURRENCY + format(total - paid);
    document.getElementById('paid-row').style.display = paid ? '' : 'none';
  }

  body.addEventListener('input', function (event) {
    if (event.target.classList.contains('f-description')) { autoGrow(event.target); }
    recalc();
  });
  document.getElementById('gst-rate').addEventListener('input', recalc);
  document.getElementById('amount-paid').addEventListener('input', recalc);

  body.addEventListener('click', function (event) {
    if (!event.target.classList.contains('remove')) { return; }
    event.target.closest('.item-row').remove();
    if (!body.querySelector('.item-row')) { addRow(null); }
    renumber();
    recalc();
  });

  document.getElementById('add-row').addEventListener('click', function () {
    addRow(null).querySelector('.f-code').focus();
  });

  /* Moving the issue date pulls the due date along by the usual terms. */
  var issue = document.getElementById('issue-date');
  var due = document.getElementById('due-date');
  issue.addEventListener('change', function () {
    if (!issue.value) { return; }
    var d = new Date(issue.value + 'T00:00:00');
    d.setDate(d.getDate() + (window.PAYMENT_TERMS_DAYS || 20));
    due.value = d.toISOString().slice(0, 10);
  });

  (window.INVOICE_ITEMS || []).forEach(addRow);
  if (!body.querySelector('.item-row')) { addRow(null); }
  recalc();
})();
