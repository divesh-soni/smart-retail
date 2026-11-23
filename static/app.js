"use strict";

const $ = (id) => document.getElementById(id);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// product names come from users so escape them
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const num = (n) => (Number.isInteger(n) ? String(n) : Number(n).toFixed(1));
const plural = (n, word) => `${num(n)} ${word}${Number(n) === 1 ? "" : "s"}`;
const icon = (name, cls = "") => `<svg class="i ${cls}"><use href="#i-${name}"/></svg>`;

function humanDays(n) {
  if (n <= 0) return "Expired";
  if (n < 60) return plural(n, "day");
  return plural(Math.round(n / 30), "month");
}

// localStorage can throw in private mode
const store = {
  get(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* ignore */ } },
};

const state = {
  data: null,
  threshold: Number(store.get("threshold", 3)),
  inv: { q: "", stock: "all", expiry: "all", sort: "severity", dir: "desc", page: 1, size: 15 },
  editing: null, // sku being edited, null when adding
};

let inFlight = 0;
function setLoading(delta) {
  inFlight += delta;
  $("progress").hidden = inFlight === 0;
}

async function api(method, url, body) {
  const options = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) options.body = JSON.stringify(body);
  setLoading(1);
  try {
    let res;
    try {
      res = await fetch(url, options);
    } catch {
      throw Object.assign(new Error("Can't reach the server. Is the app still running?"), { fields: {} });
    }
    if (res.status === 401) {
      location.href = "/login";
      throw Object.assign(new Error("Please sign in again"), { fields: {} });
    }
    const json = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw Object.assign(new Error(json.error || `Request failed (${res.status})`), { fields: json.fields || {} });
    }
    return json;
  } finally {
    setLoading(-1);
  }
}

function toast(text, type = "ok") {
  const el = document.createElement("div");
  el.className = "toast " + type;
  el.setAttribute("role", type === "error" ? "alert" : "status");
  el.innerHTML = `${icon(type === "error" ? "alert" : type === "ok" ? "check" : "info")}<span>${esc(text)}</span>`;
  $("toasts").appendChild(el);
  while ($("toasts").children.length > 3) $("toasts").firstElementChild.remove(); // max 3 at a time
  setTimeout(() => el.remove(), type === "error" ? 6000 : 3500);
}

async function withBusy(btn, fn) {
  if (!btn) return fn();
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  try { return await fn(); } finally { btn.disabled = false; btn.removeAttribute("aria-busy"); }
}

function confirmDialog({ title, text, ok = "Confirm" }) {
  const dlg = $("confirm-dialog");
  $("confirm-title").textContent = title;
  $("confirm-text").textContent = text;
  $("confirm-ok").textContent = ok;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise((resolve) => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true });
  });
}

function showFieldErrors(form, fields) {
  for (const [name, msg] of Object.entries(fields || {})) {
    const input = form.elements[name];
    if (!input || !input.closest) continue;
    input.classList.add("invalid");
    input.setAttribute("aria-invalid", "true");
    const span = document.createElement("span");
    span.className = "field-error";
    span.textContent = msg;
    input.after(span);
  }
}
function clearFieldErrors(form) {
  $$(".field-error", form).forEach((el) => el.remove());
  $$("input", form).forEach((el) => { el.classList.remove("invalid"); el.removeAttribute("aria-invalid"); });
}

function statusBadges(p) {
  const b = [];
  if (p.low_stock) b.push('<span class="badge low">Low stock</span>');
  if (p.expired) b.push('<span class="badge expired">Expired</span>');
  else if (p.near_expiry) b.push('<span class="badge near">Near expiry</span>');
  if (!b.length) b.push('<span class="badge ok">In stock</span>');
  return `<div class="badges">${b.join("")}</div>`;
}

const severity = (p) => (p.expired ? 3 : p.low_stock ? 2 : p.near_expiry ? 1 : 0);

function coverCell(p) {
  if (p.days_of_cover === null) return '<span class="t-muted">No sales</span>';
  const cls = p.days_of_cover < p.lead_time_days ? "t-critical" : "";
  return `<span class="${cls}">${num(p.days_of_cover)} d</span>`;
}

function expiryCell(p) {
  if (p.expired) return '<span class="t-expired">Expired</span>';
  if (p.near_expiry) return `<span class="t-warning">${p.expiry_days} d</span>`;
  return `${p.expiry_days} d`;
}

function productCell(p, withSku = true) {
  return `<div class="prod"><button class="linkish" data-view="${esc(p.sku)}">${esc(p.name)}</button>${withSku ? `<small>${esc(p.sku)}</small>` : ""}</div>`;
}

const startButtons = () => `<button class="btn btn-primary sm" data-action="add-product">${icon("plus")}Add product</button>
      <button class="btn btn-secondary sm" data-action="load-samples">Load sample products</button>`;

const bySku = () => Object.fromEntries(state.data.products.map((p) => [p.sku, p]));

const PAGES = ["overview", "inventory", "expiry", "reorder", "insights", "assistant", "settings"];
const ALIASES = { home: "overview", products: "inventory", orders: "reorder" }; // old links

function showPage(name) {
  name = ALIASES[name] || name;
  if (!PAGES.includes(name)) name = "overview";
  PAGES.forEach((p) => { $("page-" + p).hidden = p !== name; });
  $$(".nav-item").forEach((a) => {
    const on = a.dataset.page === name;
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  const title = $("page-" + name).dataset.title;
  $("page-title").textContent = title;
  $("crumb-current").textContent = title;
  document.title = `${title} · Smart Retail`;
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  setNav(false);
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", () => showPage(location.hash.slice(1)));

function renderOverview() {
  const { products, priority, purchase_list: orders, insights, threshold } = state.data;
  const low = products.filter((p) => p.low_stock).length;
  const near = products.filter((p) => p.near_expiry && !p.expired).length;
  const expired = products.filter((p) => p.expired).length;
  const units = orders.reduce((s, i) => s + i.qty, 0);

  $("k-total").textContent = products.length;
  $("k-low").textContent = low;
  $("k-low-note").textContent = low ? `${units} units to order` : "All above reorder level";
  $("k-near").textContent = near;
  $("k-near-note").textContent = `Within ${plural(threshold, "day")}`;
  $("k-expired").textContent = expired;
  $("k-expired-note").textContent = expired ? "Remove from shelves" : "None on shelves";

  // already sorted on the server
  const flagged = priority.filter((p) => p.flagged);
  $("prio-count").textContent = flagged.length ? plural(flagged.length, "item") : "Nothing urgent";
  if (!products.length) {
    $("prio-body").innerHTML = `<tr class="state-row"><td colspan="5">
      <span class="empty-title">Your inventory is empty</span>
      Add products to see what to reorder and what's expiring.<br>${startButtons()}</td></tr>`;
  } else if (!flagged.length) {
    $("prio-body").innerHTML = `<tr class="state-row"><td colspan="5">
      <span class="empty-title">Nothing needs attention</span>
      No low stock and nothing expiring within ${plural(threshold, "day")}.</td></tr>`;
  } else {
    $("prio-body").innerHTML = flagged.map((p) => {
      const cta = p.low_stock && !p.expired
        ? `<a class="btn btn-secondary sm" href="#reorder">Review reorder</a>`
        : `<a class="btn btn-secondary sm" href="#expiry">Review expiry</a>`;
      return `<tr>
        <td class="cell-product">${productCell(p)}</td>
        <td class="num" data-label="Stock"><span class="stack-num"><b>${p.stock}</b><small>${coverCell(p)} cover</small></span></td>
        <td class="num" data-label="Expires in">${expiryCell(p)}</td>
        <td class="reason-cell">${statusBadges(p)}<small>${esc(p.reason)}</small></td>
        <td class="action-cell"><span>${esc(p.action)}</span>${cta}</td>
      </tr>`;
    }).join("");
  }

  const h = insights.health;
  const segs = [["expired", "Expired", h.expired], ["low", "Below reorder level", h.low],
                ["near", "Near expiry only", h.expiring], ["ok", "In stock", h.ok]];
  $("health-total").textContent = plural(h.total, "product");
  $("health-bar").innerHTML = segs.filter((s) => s[2] > 0).map(([k, label, n]) =>
    `<span class="seg-${k}" style="flex-grow:${n}" data-tip="${label}: ${plural(n, "product")}"></span>`).join("");
  const pct = (n) => (h.total ? Math.round((n / h.total) * 100) + "%" : "–");
  const dotClass = { expired: "expired", low: "critical", near: "warning", ok: "ok" };
  $("health-legend").innerHTML = segs.map(([k, label, n]) =>
    `<li><i class="dot ${dotClass[k]}"></i>${label}<b>${n}</b><span class="pct">${pct(n)}</span></li>`).join("");

  const watch = products.filter((p) => p.low_stock && p.reorder_qty === 0).length;
  $("reorder-facts").innerHTML = `
    <div><dt>Products to reorder</dt><dd>${orders.length}</dd></div>
    <div><dt>Total units</dt><dd>${units}</dd></div>
    <div><dt>Low stock, no order needed</dt><dd>${watch}</dd></div>`;

  const soon = products.filter((p) => p.near_expiry).sort((a, b) => a.expiry_days - b.expiry_days).slice(0, 5);
  $("expiring-next").innerHTML = soon.length
    ? soon.map((p) => `<li><i class="dot ${p.expired ? "expired" : "warning"}"></i>
        <span class="grow"><button class="linkish" data-view="${esc(p.sku)}">${esc(p.name)}</button></span>
        <b class="${p.expired ? "t-expired" : "t-warning"}">${humanDays(p.expiry_days)}</b></li>`).join("")
    : `<li class="empty">Nothing expires within ${plural(threshold, "day")}.</li>`;
}

const SORTERS = {
  name: (p) => p.name.toLowerCase(),
  sku: (p) => p.sku.toLowerCase(),
  stock: (p) => p.stock,
  avg_daily_sales: (p) => p.avg_daily_sales,
  cover: (p) => (p.days_of_cover === null ? Infinity : p.days_of_cover),
  expiry_days: (p) => p.expiry_days,
  reorder_qty: (p) => p.reorder_qty,
  severity: (p) => severity(p),
};

function filteredInventory() {
  const { q, stock, expiry, sort, dir } = state.inv;
  const query = q.trim().toLowerCase();
  const rows = state.data.products.filter((p) =>
    (!query || p.name.toLowerCase().includes(query) || p.sku.toLowerCase().includes(query)) &&
    (stock === "all" || (stock === "low" ? p.low_stock : !p.low_stock)) &&
    (expiry === "all" || (expiry === "expired" ? p.expired : expiry === "near" ? p.near_expiry && !p.expired : !p.near_expiry)));
  const key = SORTERS[sort];
  const sign = dir === "asc" ? 1 : -1;
  return rows.sort((a, b) => {
    const x = key(a), y = key(b);
    if (x < y) return -sign;
    if (x > y) return sign;
    return a.name.localeCompare(b.name);
  });
}

function renderInventory() {
  const all = state.data.products;
  const rows = filteredInventory();
  const { page, size, q, stock, expiry } = state.inv;
  const pages = Math.max(1, Math.ceil(rows.length / size));
  state.inv.page = Math.min(page, pages);
  const start = (state.inv.page - 1) * size;
  const visible = rows.slice(start, start + size);
  const filtered = q || stock !== "all" || expiry !== "all";

  $("inv-clear").hidden = !filtered;
  $("inv-count").textContent = filtered ? `${rows.length} of ${plural(all.length, "product")}` : plural(all.length, "product");

  $$("#inv-table th[data-sort]").forEach((th) => {
    if (th.dataset.sort === state.inv.sort) th.setAttribute("aria-sort", state.inv.dir === "asc" ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  });

  if (!all.length) {
    $("inv-body").innerHTML = `<tr class="state-row"><td colspan="9"><span class="empty-title">No products yet</span>
      Add a product or load the sample data.<br>${startButtons()}</td></tr>`;
  } else if (!rows.length) {
    $("inv-body").innerHTML = `<tr class="state-row"><td colspan="9"><span class="empty-title">No matching products</span>
      Try another search.<br><button class="btn btn-secondary sm" data-action="clear-filters">Clear filters</button></td></tr>`;
  } else {
    $("inv-body").innerHTML = visible.map((p) => `<tr>
      <td>${productCell(p, false)}</td>
      <td class="sku">${esc(p.sku)}</td>
      <td class="num">${p.stock}</td>
      <td class="num">${num(p.avg_daily_sales)}</td>
      <td class="num">${coverCell(p)}</td>
      <td class="num">${expiryCell(p)}</td>
      <td class="num">${p.low_stock ? (p.reorder_qty ? `<b>${p.reorder_qty}</b>` : "0") : '<span class="t-muted">–</span>'}</td>
      <td>${statusBadges(p)}</td>
      <td class="actions">
        <button class="icon-btn" data-edit="${esc(p.sku)}" title="Edit" aria-label="Edit ${esc(p.name)}">${icon("edit")}</button>
        <button class="icon-btn danger" data-delete="${esc(p.sku)}" title="Delete" aria-label="Delete ${esc(p.name)}">${icon("trash")}</button>
      </td></tr>`).join("");
  }

  $("inv-pager").hidden = rows.length <= size;
  $("inv-pager").innerHTML = `<span>Showing ${rows.length ? start + 1 : 0}–${start + visible.length} of ${rows.length}</span>
    <span class="pager-btns">
      <button class="btn btn-secondary sm" data-page-step="-1" ${state.inv.page <= 1 ? "disabled" : ""} aria-label="Previous page">${icon("chevron-left")}Previous</button>
      <button class="btn btn-secondary sm" data-page-step="1" ${state.inv.page >= pages ? "disabled" : ""} aria-label="Next page">Next${icon("chevron-right")}</button>
    </span>`;
}

const MONITOR_DAYS = 30;

function expiryTable(rows, mode) {
  return `<div class="table-scroll"><table class="table">
    <thead><tr><th>Product</th><th class="num">Expires in</th><th class="num">In stock</th><th class="num">Avg daily sales</th>
      <th class="num">Likely unsold</th><th>Recommended action</th><th class="actions"><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>${rows.map((p) => {
      const unsold = mode === "expired" ? p.stock : mode === "near" ? p.at_risk_units : null;
      const action = mode === "monitor" ? '<span class="t-muted">No action needed yet</span>' : esc(p.action);
      return `<tr>
        <td>${productCell(p)}</td>
        <td class="num">${expiryCell(p)}</td>
        <td class="num">${p.stock}</td>
        <td class="num">${num(p.avg_daily_sales)}</td>
        <td class="num">${unsold === null ? '<span class="t-muted">–</span>' : unsold ? `<b>${unsold}</b>` : "0"}</td>
        <td class="action-cell">${action}</td>
        <td class="actions"><button class="btn btn-secondary sm" data-edit="${esc(p.sku)}">Update</button></td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
}

function renderExpiry() {
  const { products, threshold } = state.data;
  const sorted = [...products].sort((a, b) => a.expiry_days - b.expiry_days);
  const expired = sorted.filter((p) => p.expired);
  const near = sorted.filter((p) => p.near_expiry && !p.expired);
  const monitor = sorted.filter((p) => !p.near_expiry && p.expiry_days <= MONITOR_DAYS);
  const later = sorted.filter((p) => !p.near_expiry && p.expiry_days > MONITOR_DAYS).length;

  const group = (cls, title, sub, rows, mode, empty) => `
    <div class="panel ${cls}">
      <div class="group-head"><div><h3>${title}</h3><p class="muted">${sub}</p></div><span class="pill">${plural(rows.length, "product")}</span></div>
      ${rows.length ? expiryTable(rows, mode) : `<p class="empty-group">${empty}</p>`}
    </div>`;

  $("expiry-groups").innerHTML = !products.length
    ? `<div class="panel"><p class="empty-group">No products yet.</p></div>`
    : group("group-critical", "Expired", "Take these off the shelf.", expired, "expired", "No expired products.") +
      group("group-warning", "Expiring soon", `Expiring within ${plural(threshold, "day")}. Sell first or discount.`, near, "near",
            `Nothing expires within ${plural(threshold, "day")}.`) +
      group("group-monitor", "Monitor", `Expiring in ${threshold + 1}-${MONITOR_DAYS} days.`, monitor, "monitor",
            `Nothing else expires in the next ${MONITOR_DAYS} days.`) +
      (later ? `<p class="muted small">${plural(later, "other product")} expire in more than ${MONITOR_DAYS} days.</p>` : "");
}

function renderReorder() {
  const products = bySku();
  const list = state.data.purchase_list.map((i) => ({ ...products[i.sku], qty: i.qty, calc: i.calc }));
  const units = list.reduce((s, i) => s + i.qty, 0);

  $("reorder-body").innerHTML = list.length
    ? list.map((p) => `<tr>
        <td>${productCell(p, false)}</td>
        <td class="sku">${esc(p.sku)}</td>
        <td class="num">${p.stock}</td>
        <td class="num">${num(p.avg_daily_sales)}</td>
        <td class="num">${p.lead_time_days} d</td>
        <td class="num">${coverCell(p)}</td>
        <td class="num qty">${p.qty}</td>
        <td class="why"><small>${esc(p.reason)}</small><code>${esc(p.calc)}</code></td>
        <td class="print-only">&#9744;</td>
      </tr>`).join("")
    : `<tr class="state-row"><td colspan="9"><span class="empty-title">Nothing to reorder</span>
        ${state.data.products.length ? "Stock is enough for now." : "Add products first."}</td></tr>`;
  $("reorder-foot").innerHTML = list.length
    ? `<tr><td colspan="6">Total: ${plural(list.length, "product")}</td><td class="num">${units}</td><td></td><td class="print-only"></td></tr>` : "";

  const watch = state.data.products.filter((p) => p.low_stock && p.reorder_qty === 0);
  $("reorder-watch").hidden = !watch.length;
  $("reorder-watch-list").innerHTML = watch.map((p) =>
    `<li><span class="grow"><button class="linkish" data-view="${esc(p.sku)}">${esc(p.name)}</button> <span class="t-muted">${esc(p.sku)}</span></span><span class="muted">${esc(p.reason)}</span></li>`).join("");

  const shop = store.get("shopName", "");
  $("print-meta").textContent = `${shop ? shop + " · " : ""}${new Date().toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" })}`;
}

const REC_ICONS = { order: "cart", remove: "trash", discount: "tag", overstock: "box-stack",
                    reorder_level: "clock", not_selling: "pause", slow: "trend-down" };

function renderInsights() {
  const { insights, metrics, products } = state.data;

  const groups = [["urgent", "Act today"], ["warning", "This week"], ["tip", "Improve over time"]];
  const html = groups.map(([level, title]) => {
    const items = insights.suggestions.filter((s) => s.level === level);
    if (!items.length) return "";
    return `<div class="rec-group-title">${title} <span class="pill">${items.length}</span></div>` +
      items.map((s) => `<div class="rec ${level}">
        <span class="rec-icon">${icon(REC_ICONS[s.kind] || "info")}</span>
        <div class="rec-body"><b>${esc(s.title)}</b><p>${esc(s.detail)}</p></div>
        <button class="btn btn-secondary sm" data-view="${esc(s.sku)}">View product</button>
      </div>`).join("");
  }).join("");
  $("recommendations").innerHTML = html ||
    `<p class="empty-group">${products.length ? "Nothing to suggest right now." : "Add products first."}</p>`;

  $("best-sellers").innerHTML = insights.best_sellers.length
    ? insights.best_sellers.map((b, i) => `<li><span class="rank">${i + 1}</span>
        <span class="grow"><button class="linkish" data-view="${esc(b.sku)}">${esc(b.name)}</button></span><b>${num(b.per_day)}</b></li>`).join("")
    : '<li class="empty">No sales recorded yet.</li>';

  $("rule-eval").innerHTML = `
    <div><dt>Issues found</dt><dd>${metrics.total_flags} on ${plural(metrics.products_flagged, "product")}</dd></div>
    <div><dt>Low-stock flags</dt><dd>${metrics.low_stock_flags}</dd></div>
    <div><dt>Near-expiry flags</dt><dd>${metrics.near_expiry_flags}</dd></div>
    <div><dt>Low-stock-only baseline</dt><dd>finds ${metrics.baseline_flags} of ${metrics.total_flags}</dd></div>
    <div><dt>False alarms</dt><dd>${metrics.false_alarms}</dd></div>`;

  renderCharts();
}

const CHART_MAX_DAYS = 14; // longer bars get cut off

function barRow(name, sku, pct, cls, value, tip, extra = "", capped = false) {
  return `<div class="bar-row">
    <div class="name"><button class="linkish" data-view="${esc(sku)}" title="${esc(name)}">${esc(name)}</button></div>
    <div class="track" data-tip="${esc(tip)}"><div class="bar ${cls} ${capped ? "capped" : ""}" style="width:${pct}%"></div>${extra}</div>
    <div class="val">${value}</div></div>`;
}
const axis = () => `<div class="axis"><span></span><div><span>0</span><span>${CHART_MAX_DAYS / 2} days</span><span>${CHART_MAX_DAYS}+ days</span></div><span></span></div>`;
const pctOf = (d) => (Math.min(d, CHART_MAX_DAYS) / CHART_MAX_DAYS) * 100;

function renderCharts() {
  const products = state.data.products;
  if (!products.length) {
    $("cover-chart").innerHTML = $("expiry-chart").innerHTML = '<p class="empty-note">No products to chart yet.</p>';
    return;
  }
  const byCover = [...products].sort((a, b) => (a.days_of_cover ?? Infinity) - (b.days_of_cover ?? Infinity));
  $("cover-chart").innerHTML = byCover.map((p) => {
    const marker = `<div class="marker" style="left:${pctOf(p.lead_time_days)}%"></div>`;
    if (p.days_of_cover === null) {
      return barRow(p.name, p.sku, 100, "ok", "No sales", `${p.name}: no sales yet`, marker, true);
    }
    const c = p.days_of_cover;
    const cls = c < p.lead_time_days ? "critical" : p.low_stock ? "warning" : "ok";
    return barRow(p.name, p.sku, pctOf(c), cls, `${num(c)} d`,
      `${p.name}: lasts ${plural(c, "day")}, delivery takes ${plural(p.lead_time_days, "day")}`,
      marker, c > CHART_MAX_DAYS);
  }).join("") + axis();

  const byExpiry = [...products].sort((a, b) => a.expiry_days - b.expiry_days);
  $("expiry-chart").innerHTML = byExpiry.map((p) => {
    const cls = p.expired ? "expired" : p.near_expiry ? "warning" : "ok";
    const tip = p.expired ? `${p.name} has expired.` : `${p.name} expires in ${humanDays(p.expiry_days).toLowerCase()}.`;
    return barRow(p.name, p.sku, Math.max(1.5, pctOf(p.expiry_days)), cls, humanDays(p.expiry_days), tip, "", p.expiry_days > CHART_MAX_DAYS);
  }).join("") + axis();
}

function renderHeader() {
  const { priority, products, purchase_list: orders } = state.data;
  const flagged = priority.filter((p) => p.flagged);
  $("alerts-count").hidden = !flagged.length;
  $("alerts-count").textContent = flagged.length > 99 ? "99+" : flagged.length;
  $("alerts-btn").setAttribute("aria-label", flagged.length ? `Inventory alerts: ${flagged.length}` : "Inventory alerts: none");
  $("alerts-sub").textContent = flagged.length ? `${plural(flagged.length, "product")} need attention` : "No active alerts";
  $("alerts-list").innerHTML = flagged.length
    ? flagged.slice(0, 8).map((p) => `<button class="menu-item alert-item" data-view="${esc(p.sku)}">
        <i class="dot ${p.expired ? "expired" : p.low_stock ? "critical" : "warning"}" style="margin-top:6px"></i>
        <span class="alert-text"><b>${esc(p.name)}</b><small>${esc(p.action)}</small></span></button>`).join("")
    : '<p class="menu-empty">No alerts.</p>';

  const expiring = products.filter((p) => p.near_expiry).length;
  $("nav-expiry-count").hidden = !expiring;
  $("nav-expiry-count").textContent = expiring;
  $("nav-reorder-count").hidden = !orders.length;
  $("nav-reorder-count").textContent = orders.length;
}

function renderSearchResults() {
  const box = $("search-results");
  const q = $("global-search").value.trim().toLowerCase();
  if (!q || !state.data) { box.hidden = true; $("global-search").setAttribute("aria-expanded", "false"); return; }
  const hits = state.data.products.filter((p) => p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q));
  box.innerHTML = (hits.length
    ? hits.slice(0, 7).map((p) => `<button class="menu-item" role="option" data-view="${esc(p.sku)}">
        <span><b>${esc(p.name)}</b> <span class="sku">${esc(p.sku)}</span></span><span class="result-meta">${statusBadges(p)}</span></button>`).join("")
    : '<p class="menu-empty">No products match.</p>') +
    `<button class="menu-foot menu-item" data-search-all>See all in Inventory</button>`;
  box.hidden = false;
  $("global-search").setAttribute("aria-expanded", "true");
}

function searchInInventory(q) {
  state.inv.q = q;
  state.inv.page = 1;
  $("inv-search").value = q;
  $("global-search").value = "";
  $("search-results").hidden = true;
  showPage("inventory");
  if (state.data) renderInventory();
}

const BASE_QUESTIONS = ["Which items are most urgent today?", "Which products need reordering?",
                        "Which products are nearing expiry?", "What are my best sellers?",
                        "Which products are moving slowly?", "Give me a summary"];

function renderSuggestions() {
  const qs = [...BASE_QUESTIONS];
  const low = state.data && state.data.priority.find((p) => p.low_stock);
  if (low) qs.splice(3, 0, `Why is ${low.name} marked as low stock?`);
  $("suggestions").innerHTML = qs.map((q) =>
    `<button class="suggestion" data-question="${esc(q)}"><span>${esc(q)}</span>${icon("arrow-right")}</button>`).join("");
}

function addMessage(kind, who, html) {
  const el = document.createElement("div");
  el.className = "msg " + kind;
  el.innerHTML = `<div class="msg-who">${esc(who)}</div><div class="msg-body">${html}</div>`;
  $("chat").appendChild(el);
  $("chat").scrollTop = $("chat").scrollHeight;
  return el;
}

function answerHtml(a) {
  const rows = a.items.map((i) => `<tr>
      <td><span class="lvl ${esc(i.level)}"></span>${esc(i.title)}</td>
      <td>${esc(i.detail)}</td>
      <td>${i.sku ? `<button class="btn btn-ghost sm" data-view="${esc(i.sku)}">View</button>` : ""}</td></tr>`).join("");
  return `${esc(a.answer)}${rows ? `<table class="answer-table"><tbody>${rows}</tbody></table>` : ""}`;
}

async function ask(question) {
  question = question.trim();
  if (!question) return;
  addMessage("user", "You", esc(question));
  const pending = addMessage("bot loading", "Assistant", "Checking…");
  await withBusy($("ask-btn"), async () => {
    try {
      const a = await api("POST", "/api/ask", { question, threshold: state.threshold });
      pending.className = "msg bot";
      pending.querySelector(".msg-body").innerHTML = answerHtml(a);
    } catch (err) {
      pending.className = "msg bot error";
      pending.querySelector(".msg-body").textContent = `Couldn't get an answer: ${err.message}`;
    }
  });
  $("chat").scrollTop = $("chat").scrollHeight;
}

const FIELDS = ["name", "sku", "stock", "avg_daily_sales", "lead_time_days", "expiry_days", "reorder_level"];

function openProduct(sku = null) {
  const form = $("product-form");
  const p = sku && state.data ? state.data.products.find((x) => x.sku === sku) : null;
  if (sku && !p) { toast("That product no longer exists.", "error"); return; }
  state.editing = p ? p.sku : null;
  form.reset();
  clearFieldErrors(form);
  $("product-form-error").hidden = true;

  $("product-dialog-title").textContent = p ? "Edit product" : "Add product";
  $("product-dialog-sub").textContent = p ? `${p.name} · ${p.sku}` : "Enter stock and sales.";
  $("product-delete").hidden = !p;
  form.elements.sku.readOnly = !!p; // sku can't change
  if (p) FIELDS.forEach((f) => { form.elements[f].value = p[f]; });

  const preview = $("product-preview");
  preview.hidden = !p;
  if (p) {
    preview.innerHTML = `${statusBadges(p)}
      <span>Days of cover: <b>${p.days_of_cover === null ? "no sales" : num(p.days_of_cover) + " days"}</b> · Expires in: <b>${humanDays(p.expiry_days)}</b></span>
      ${p.flagged ? `<span>Recommended: <b>${esc(p.action)}</b></span>` : "<span>No action needed right now.</span>"}`;
  }
  $("product-dialog").showModal();
  (p ? form.elements.stock : form.elements.name).focus();
}

async function saveProduct(e) {
  e.preventDefault();
  const form = $("product-form");
  clearFieldErrors(form);
  $("product-form-error").hidden = true;
  const body = Object.fromEntries(new FormData(form).entries());
  await withBusy($("product-save"), async () => {
    try {
      if (state.editing) {
        delete body.sku;
        await api("PUT", "/api/products/" + encodeURIComponent(state.editing), body);
        toast(`${body.name} updated`);
      } else {
        await api("POST", "/api/products", body);
        toast(`${body.name} added`);
      }
      $("product-dialog").close();
      await load();
    } catch (err) {
      showFieldErrors(form, err.fields);
      if (!Object.keys(err.fields || {}).length || err.fields._) {
        $("product-form-error").textContent = err.message;
        $("product-form-error").hidden = false;
      }
      const first = form.querySelector(".invalid");
      if (first) first.focus();
    }
  });
}

async function deleteProduct(sku, btn) {
  const p = state.data.products.find((x) => x.sku === sku);
  if (!p) return;
  const ok = await confirmDialog({ title: `Delete ${p.name}?`, text: `${p.name} (${p.sku}) will be deleted. This can't be undone.`, ok: "Delete product" });
  if (!ok) return false;
  await withBusy(btn, async () => {
    try {
      await api("DELETE", "/api/products/" + encodeURIComponent(sku));
      toast(`${p.name} deleted`);
      await load();
    } catch (err) { toast(err.message, "error"); }
  });
  return true;
}

function updateExportLinks() {
  $$("[data-export]").forEach((a) => {
    a.href = `/export/${a.dataset.export}.csv?threshold=${encodeURIComponent(state.threshold)}`;
  });
}

async function load() {
  try {
    state.data = await api("GET", "/api/products?threshold=" + encodeURIComponent(state.threshold));
    $("load-error").hidden = true;
    renderHeader();
    renderOverview();
    renderInventory();
    renderExpiry();
    renderReorder();
    renderInsights();
    renderSuggestions();
    updateExportLinks();
  } catch (err) {
    $("load-error-text").textContent = `Couldn't load data. ${err.message}`;
    $("load-error").hidden = false;
    $$(".state-row td").forEach((td) => { td.textContent = "Data unavailable."; });
  }
}

// sidebar on small screens
function setNav(open) {
  document.body.classList.toggle("nav-open", open);
  $("scrim").hidden = !open;
  $("menu-btn").setAttribute("aria-expanded", String(open));
}
$("menu-btn").addEventListener("click", () => setNav(!document.body.classList.contains("nav-open")));
$("scrim").addEventListener("click", () => setNav(false));

function closeMenus(except) {
  $$("[data-dropdown]").forEach((btn) => {
    if (btn === except) return;
    btn.setAttribute("aria-expanded", "false");
    $(btn.dataset.dropdown).hidden = true;
  });
}
$$("[data-dropdown]").forEach((btn) => btn.addEventListener("click", (e) => {
  e.stopPropagation();
  const menu = $(btn.dataset.dropdown);
  closeMenus(btn);
  menu.hidden = !menu.hidden;
  btn.setAttribute("aria-expanded", String(!menu.hidden));
}));

document.addEventListener("click", (e) => {
  if (!e.target.closest(".dropdown")) closeMenus();
  if (!e.target.closest("#search-box")) $("search-results").hidden = true;

  // delegated because rows get re-rendered
  const view = e.target.closest("[data-view]");
  if (view && view.dataset.view) {
    closeMenus();
    if (view.closest("#search-results")) $("global-search").value = "";
    $("search-results").hidden = true;
    openProduct(view.dataset.view);
    return;
  }
  const edit = e.target.closest("[data-edit]");
  if (edit) { openProduct(edit.dataset.edit); return; }
  const del = e.target.closest("[data-delete]");
  if (del) { deleteProduct(del.dataset.delete, del); return; }
  const action = e.target.closest("[data-action]");
  if (action) {
    if (action.dataset.action === "add-product") openProduct();
    if (action.dataset.action === "load-samples") loadSamples(action);
    if (action.dataset.action === "clear-filters") clearFilters();
    return;
  }
  const step = e.target.closest("[data-page-step]");
  if (step) { state.inv.page += Number(step.dataset.pageStep); renderInventory(); return; }
  if (e.target.closest("[data-search-all]")) { searchInInventory($("global-search").value); return; }
  const q = e.target.closest("[data-question]");
  if (q) { ask(q.dataset.question); }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeMenus(); $("search-results").hidden = true; setNav(false); }
  // "/" jumps to search
  if (e.key === "/" && !e.target.closest("input, textarea, select, dialog")) { e.preventDefault(); $("global-search").focus(); }
});

$("global-search").addEventListener("input", renderSearchResults);
$("global-search").addEventListener("focus", renderSearchResults);
$("global-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); searchInInventory($("global-search").value); }
});

function clearFilters() {
  Object.assign(state.inv, { q: "", stock: "all", expiry: "all", page: 1 });
  $("inv-search").value = "";
  $("inv-stock").value = "all";
  $("inv-expiry").value = "all";
  renderInventory();
}
function onFilter(id, key, event) {
  $(id).addEventListener(event, (e) => { state.inv[key] = e.target.value; state.inv.page = 1; if (state.data) renderInventory(); });
}
onFilter("inv-search", "q", "input");
onFilter("inv-stock", "stock", "change");
onFilter("inv-expiry", "expiry", "change");
$("inv-clear").addEventListener("click", clearFilters);
$$("#inv-table th[data-sort] button").forEach((b) => b.addEventListener("click", () => {
  const key = b.closest("th").dataset.sort;
  if (state.inv.sort === key) state.inv.dir = state.inv.dir === "asc" ? "desc" : "asc";
  else { state.inv.sort = key; state.inv.dir = key === "name" || key === "sku" ? "asc" : "desc"; }
  if (state.data) renderInventory();
}));

$("product-form").addEventListener("submit", saveProduct);
$$("dialog [data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close("cancel")));
$("product-delete").addEventListener("click", async (e) => {
  const sku = state.editing;
  $("product-dialog").close();
  const deleted = await deleteProduct(sku, e.currentTarget);
  if (deleted === false) openProduct(sku); // cancelled, show the form again
});
// click outside closes the dialog
$$("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close("cancel"); }));

let thresholdTimer;
$$("[data-threshold]").forEach((input) => {
  input.value = state.threshold;
  input.addEventListener("input", () => {
    const v = Number(input.value);
    if (input.value === "" || !Number.isInteger(v) || v < 0 || v > 365) { input.classList.add("invalid"); return; }
    input.classList.remove("invalid");
    $$("[data-threshold]").forEach((other) => { if (other !== input) other.value = v; });
    state.threshold = v;
    store.set("threshold", v);
    clearTimeout(thresholdTimer);
    thresholdTimer = setTimeout(load, 400);
  });
});

$("print-btn").addEventListener("click", () => window.print());

$("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("ask-input").value;
  $("ask-input").value = "";
  ask(q);
});

$("shop-input").value = store.get("shopName", "");
$("shop-input").addEventListener("input", (e) => {
  store.set("shopName", e.target.value.trim());
  if (state.data) renderReorder();
});

$("pw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  clearFieldErrors(form);
  await withBusy(form.querySelector("button[type=submit]"), async () => {
    try {
      await api("POST", "/api/account/password", Object.fromEntries(new FormData(form).entries()));
      form.reset();
      toast("Password updated");
    } catch (err) {
      showFieldErrors(form, err.fields);
      toast(err.message, "error");
    }
  });
});

async function confirmAndRun(btn, question, method, url, done) {
  if (!(await confirmDialog(question))) return;
  await withBusy(btn, async () => {
    try { await api(method, url); toast(done); await load(); }
    catch (err) { toast(err.message, "error"); }
  });
}

function loadSamples(btn) {
  return confirmAndRun(btn, { title: "Load sample products?", text: "This replaces all products with 6 sample ones. Your account stays.", ok: "Load samples" },
    "POST", "/api/reset", "Sample products loaded");
}
$("reset-btn").addEventListener("click", (e) => loadSamples(e.currentTarget));

$("clear-btn").addEventListener("click", (e) => {
  const count = state.data ? state.data.products.length : 0;
  confirmAndRun(e.currentTarget, { title: "Remove all products?", text: `${plural(count, "product")} will be deleted for good.`, ok: "Remove all" },
    "DELETE", "/api/products", "All products removed");
});

$("retry-btn").addEventListener("click", (e) => withBusy(e.currentTarget, load));

// chart tooltips
const tooltip = $("tooltip");
document.addEventListener("mousemove", (e) => {
  const t = e.target.closest("[data-tip]");
  if (!t) { tooltip.hidden = true; return; }
  tooltip.textContent = t.dataset.tip;
  tooltip.hidden = false;
  tooltip.style.left = Math.min(e.clientX + 14, window.innerWidth - tooltip.offsetWidth - 8) + "px";
  tooltip.style.top = e.clientY + 16 + "px";
});

(async function init() {
  $("today-date").textContent = "· " + new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  updateExportLinks();
  renderSuggestions();
  addMessage("bot", "Assistant", "Ask me about stock, reordering or expiry. Pick a question or type your own.");
  showPage(location.hash.slice(1) || "overview");
  load();
})();
