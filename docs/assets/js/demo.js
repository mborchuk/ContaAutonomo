/**
 * demo.js — runtime renderer for the Demo data page.
 *
 * Contract
 * --------
 *  - On DOMContentLoaded, fetch demo_data.json and render four sections:
 *      • #demo-invoices tbody       (first 5 invoices)
 *      • #demo-customers tbody      (first 5 customers)
 *      • #demo-expenses tbody       (first 5 expenses, with contractor lookup)
 *      • #demo-tax-breakdown        (IVA / IRPF / Social Security with formulas)
 *  - On any failure (network, HTTP != ok, JSON parse), reveal #demo-error
 *    with the text "Demo data unavailable: failed to load demo_data.json."
 *
 * Data URL resolution order
 * -------------------------
 *  1. element with [data-demo-url] (e.g. <body data-demo-url="...">)
 *  2. <meta name="demo-data-url" content="...">
 *  3. fallback "/assets/data/demo_data.json"
 *
 * Tax formulas
 * ------------
 *      total           = Σ invoice.amount_eur
 *      iva             = total × 0.21
 *      irpf            = total × 0.20
 *      months          = |{ invoice_date[0..6] }|  (distinct YYYY-MM, min 1)
 *      social_security = 300 × months
 *
 * Templates
 * ---------
 *  If <template id="row-template-invoice|customer|expense"> elements exist
 *  in the DOM, their <tr> is cloned per row; otherwise a fresh <tr> is built.
 *
 * Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6
 *
 * Loaded with `defer`, so the DOM is parsed by the time this script runs.
 */
(() => {
  'use strict';

  const DEFAULT_URL = '/assets/data/demo_data.json';
  const ERROR_MESSAGE = 'Demo data unavailable: failed to load demo_data.json.';
  const ROW_LIMIT = 5;
  const IVA_RATE = 0.21;
  const IRPF_RATE = 0.20;
  const SOCIAL_SECURITY_PER_MONTH = 300;

  const eurFormatter = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'EUR',
  });

  /* ---------- helpers ---------- */

  function resolveDataUrl() {
    const dataAttrEl = document.querySelector('[data-demo-url]');
    if (dataAttrEl && dataAttrEl.dataset.demoUrl) {
      return dataAttrEl.dataset.demoUrl;
    }
    const meta = document.querySelector('meta[name="demo-data-url"]');
    if (meta && meta.content) {
      return meta.content;
    }
    return DEFAULT_URL;
  }

  function showError() {
    const el = document.getElementById('demo-error');
    if (!el) return;
    el.textContent = ERROR_MESSAGE;
    el.hidden = false;
  }

  function formatMoney(amount, currency) {
    const num = Number(amount) || 0;
    if (currency === 'EUR') {
      return eurFormatter.format(num);
    }
    return `${num.toFixed(2)} ${currency || ''}`.trim();
  }

  function rowFromTemplate(templateId) {
    const tmpl = document.getElementById(templateId);
    if (tmpl && 'content' in tmpl) {
      const tr = tmpl.content.querySelector('tr');
      if (tr) return tr.cloneNode(true);
    }
    return document.createElement('tr');
  }

  function appendCell(tr, text, className) {
    const td = document.createElement('td');
    if (className) td.className = className;
    td.textContent = text == null || text === '' ? '—' : String(text);
    tr.appendChild(td);
    return td;
  }

  function clearChildren(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  /* ---------- renderers ---------- */

  function renderInvoices(invoices) {
    const tbody = document.querySelector('#demo-invoices tbody');
    if (!tbody) return;
    clearChildren(tbody);
    invoices.slice(0, ROW_LIMIT).forEach((inv) => {
      const tr = rowFromTemplate('row-template-invoice');
      clearChildren(tr);
      appendCell(tr, inv.invoice_number);
      appendCell(tr, inv.client_name);
      appendCell(tr, eurFormatter.format(Number(inv.amount_eur) || 0));
      appendCell(tr, inv.currency);
      const statusTd = document.createElement('td');
      const badge = document.createElement('span');
      badge.className = `badge badge--${inv.status || 'unknown'}`;
      badge.textContent = inv.status || '—';
      statusTd.appendChild(badge);
      tr.appendChild(statusTd);
      appendCell(tr, inv.invoice_date);
      tbody.appendChild(tr);
    });
  }

  function renderCustomers(customers) {
    const tbody = document.querySelector('#demo-customers tbody');
    if (!tbody) return;
    clearChildren(tbody);
    customers.slice(0, ROW_LIMIT).forEach((c) => {
      const tr = rowFromTemplate('row-template-customer');
      clearChildren(tr);
      appendCell(tr, c.name);
      appendCell(tr, c.country);
      appendCell(tr, c.tax_type);
      appendCell(tr, c.vat_number);
      tbody.appendChild(tr);
    });
  }

  function buildContractorMap(contractors) {
    const map = new Map();
    if (!Array.isArray(contractors)) return map;
    contractors.forEach((c) => {
      if (c && c.id != null) {
        map.set(c.id, c.name || c.company_name || '');
      }
    });
    return map;
  }

  function renderExpenses(expenses, contractorMap) {
    const tbody = document.querySelector('#demo-expenses tbody');
    if (!tbody) return;
    clearChildren(tbody);
    expenses.slice(0, ROW_LIMIT).forEach((e) => {
      const tr = rowFromTemplate('row-template-expense');
      clearChildren(tr);
      appendCell(tr, e.category);
      appendCell(tr, formatMoney(e.amount, e.currency));
      appendCell(tr, e.expense_date);
      const contractor = e.contractor_id != null ? contractorMap.get(e.contractor_id) : '';
      appendCell(tr, contractor);
      tbody.appendChild(tr);
    });
  }

  function distinctMonths(invoices) {
    const set = new Set();
    invoices.forEach((inv) => {
      if (typeof inv.invoice_date === 'string' && inv.invoice_date.length >= 7) {
        set.add(inv.invoice_date.slice(0, 7));
      }
    });
    return set.size;
  }

  function renderTaxBreakdown(invoices) {
    const container = document.getElementById('demo-tax-breakdown');
    if (!container) return;
    clearChildren(container);

    const total = invoices.reduce(
      (sum, inv) => sum + (Number(inv.amount_eur) || 0),
      0,
    );
    const iva = total * IVA_RATE;
    const irpf = total * IRPF_RATE;
    const months = distinctMonths(invoices) || 1;
    const social = SOCIAL_SECURITY_PER_MONTH * months;

    const totalStr = eurFormatter.format(total);
    const monthLabel = months === 1 ? 'month' : 'months';

    const items = [
      {
        label: `IVA (21% of ${totalStr})`,
        value: eurFormatter.format(iva),
        formula: 'iva = total × 0.21',
      },
      {
        label: `IRPF (20% of ${totalStr})`,
        value: eurFormatter.format(irpf),
        formula: 'irpf = total × 0.20',
      },
      {
        label: `Social Security (€300 × ${months} ${monthLabel})`,
        value: eurFormatter.format(social),
        formula: 'social_security = 300 × months',
      },
    ];

    const ul = document.createElement('ul');
    ul.className = 'tax-breakdown';
    items.forEach((item) => {
      const li = document.createElement('li');
      const label = document.createElement('span');
      label.className = 'tax-breakdown__label';
      label.textContent = `${item.label}: `;
      const value = document.createElement('strong');
      value.className = 'tax-breakdown__value';
      value.textContent = item.value;
      const formula = document.createElement('code');
      formula.className = 'tax-breakdown__formula';
      formula.textContent = item.formula;
      li.appendChild(label);
      li.appendChild(value);
      li.appendChild(document.createTextNode(' '));
      li.appendChild(formula);
      ul.appendChild(li);
    });
    container.appendChild(ul);

    const totalLine = document.createElement('p');
    totalLine.className = 'tax-breakdown__total';
    totalLine.textContent =
      `Total invoiced: ${totalStr} across ${months} ${monthLabel}.`;
    container.appendChild(totalLine);
  }

  function renderAll(data) {
    const tables = (data && data.tables) || {};
    const invoices = Array.isArray(tables.invoice) ? tables.invoice : [];
    const customers = Array.isArray(tables.customer) ? tables.customer : [];
    const expenses = Array.isArray(tables.expense) ? tables.expense : [];
    const contractorMap = buildContractorMap(tables.contractor);
    renderInvoices(invoices);
    renderCustomers(customers);
    renderExpenses(expenses, contractorMap);
    renderTaxBreakdown(invoices);
  }

  /* ---------- entry point ---------- */

  async function loadDemoData() {
    const url = resolveDataUrl();
    try {
      const response = await fetch(url, { credentials: 'same-origin' });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json(); // throws on invalid JSON
      renderAll(data);
    } catch (_err) {
      showError();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadDemoData);
  } else {
    loadDemoData();
  }
})();
