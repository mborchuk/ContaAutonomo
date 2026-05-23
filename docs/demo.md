---
layout: page
title: "Demo data — Sample invoices, customers and expenses"
description: "Live preview of demo invoices, customers, expenses and the Spanish tax breakdown rendered from demo_data.json."
permalink: /demo/
---

<p>The data below is loaded from <code>demo_data.json</code> at runtime. It illustrates the structure ContaAutónomo uses internally.</p>

<div id="demo-error" role="alert" hidden></div>
<div data-demo-url="{{ '/assets/data/demo_data.json' | relative_url }}"></div>

<h2 id="invoices">Invoices</h2>
<table id="demo-invoices">
  <caption>Sample invoices from demo_data.json</caption>
  <thead>
    <tr>
      <th scope="col">Number</th>
      <th scope="col">Client</th>
      <th scope="col">Amount (EUR)</th>
      <th scope="col">Currency</th>
      <th scope="col">Status</th>
      <th scope="col">Date</th>
    </tr>
  </thead>
  <tbody></tbody>
</table>
<template id="row-template-invoice">
  <tr>
    <td></td><td></td><td></td><td></td><td></td><td></td>
  </tr>
</template>

<h2 id="customers">Customers</h2>
<table id="demo-customers">
  <caption>Sample customers across countries and tax types</caption>
  <thead>
    <tr>
      <th scope="col">Name</th>
      <th scope="col">Country</th>
      <th scope="col">Tax type</th>
      <th scope="col">VAT number</th>
    </tr>
  </thead>
  <tbody></tbody>
</table>
<template id="row-template-customer">
  <tr><td></td><td></td><td></td><td></td></tr>
</template>

<h2 id="expenses">Expenses</h2>
<table id="demo-expenses">
  <caption>Sample expense entries with categories and amounts</caption>
  <thead>
    <tr>
      <th scope="col">Category</th>
      <th scope="col">Amount</th>
      <th scope="col">Date</th>
      <th scope="col">Contractor</th>
    </tr>
  </thead>
  <tbody></tbody>
</table>
<template id="row-template-expense">
  <tr><td></td><td></td><td></td><td></td></tr>
</template>

<h2 id="tax-breakdown">Spanish tax breakdown</h2>
<p>Computed from the demo invoices above. Formulas:</p>
<ul>
  <li><code>iva = total × 0.21</code> (Spanish VAT)</li>
  <li><code>irpf = total × 0.20</code> (income tax withholding)</li>
  <li><code>social_security = 300 × months</code> (cuota de autónomos)</li>
</ul>
<div id="demo-tax-breakdown"></div>

<script src="{{ '/assets/js/demo.js' | relative_url }}" defer></script>
