let token = localStorage.getItem("prostarm_token");
let currentUser = null;
let branches = [];
let activeView = "dashboard";
let inventoryCache = [];
let materialCache = [];

const money = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

const titles = {
  dashboard: "Inventory Dashboard",
  materials: "Material Master",
  inward: "Inward Stock",
  outward: "Outward Stock",
  dispositions: "Dispositions",
  reports: "Reports",
  imports: "Imports",
  settings: "Settings",
};

function qs(id) {
  return document.getElementById(id);
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[ch]);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(path, { ...options, headers });
  const data = await res.json();
  if (!res.ok) throw data;
  return data;
}

// ── Message display ────────────────────────────────────────────────────────
// Uses the persistent #pageMessage bar at the top of the workspace.
// Falls back to alert() if the element isn't in the DOM yet (e.g. during boot).
let _msgTimer = null;
function showMessage(text, isError = false) {
  const el = qs("pageMessage");
  if (!el) {
    if (isError) console.error(text);
    return;
  }
  if (_msgTimer) clearTimeout(_msgTimer);
  el.textContent = text;
  el.className = `page-message ${isError ? "error-box" : "success-box"}`;
  el.classList.remove("hidden");           // belt-and-suspenders: always remove hidden
  _msgTimer = setTimeout(() => {
    el.classList.add("hidden");
    _msgTimer = null;
  }, 3500);
}

function showApp() {
  qs("login").classList.add("hidden");
  qs("app").classList.remove("hidden");
}

function showLogin() {
  qs("app").classList.add("hidden");
  qs("login").classList.remove("hidden");
}

async function loadBranches() {
  branches = await api("/api/branches");
  const options = [`<option value="all">All Locations</option>`].concat(
    branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`)
  ).join("");
  qs("branchSelect").innerHTML = options;
  qs("txBranch").innerHTML = branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("");
}

async function loadMaterials() {
  materialCache = await api("/api/materials");
  return materialCache;
}

function materialOptions() {
  return materialCache.map((m) => `<option value="${m.id}">${esc(m.item_name)} (${esc(m.sku)})</option>`).join("");
}

async function setView(view) {
  activeView = view;
  qs("pageTitle").textContent = titles[view];
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  qs("dashboardView").classList.toggle("hidden", view !== "dashboard");
  qs("moduleView").classList.toggle("hidden", view === "dashboard");
  if (view === "dashboard") return loadDashboard();
  await renderModule(view);
}

async function loadDashboard() {
  const branchId = qs("branchSelect").value || "all";
  const condition = qs("conditionSelect").value || "ALL";
  const [summary, inventory] = await Promise.all([
    api(`/api/dashboard?branchId=${branchId}`),
    api(`/api/inventory?branchId=${branchId}&condition=${condition}`),
  ]);
  inventoryCache = inventory;
  qs("totalItems").textContent = summary.totalItems;
  qs("lowStockAlerts").textContent = summary.lowStockAlerts;
  qs("totalValuation").textContent = money.format(summary.totalValuation);
  qs("recentActivity").textContent = summary.recentActivity;
  renderBars(summary.byCondition);
  renderInventory(inventory);
}

function renderBars(rows) {
  const max = Math.max(1, ...rows.map((r) => Number(r.quantity)));
  qs("conditionBars").innerHTML = rows.map((r) => {
    const width = Math.round((Number(r.quantity) / max) * 100);
    return `
      <div class="bar-row">
        <strong>${esc(r.condition)}</strong>
        <span class="bar-track"><span class="bar-fill" style="width:${width}%"></span></span>
        <span>${esc(r.quantity)}</span>
      </div>`;
  }).join("");
}

function renderInventory(rows) {
  qs("inventoryRows").innerHTML = rows.map((r) => {
    const cls = r.status === "Low Stock" ? "Low" : r.status;
    return `
      <tr>
        <td>${r.id}</td>
        <td><strong>${esc(r.sku)}</strong></td>
        <td>${esc(r.item_name)}</td>
        <td>${esc(r.category)}</td>
        <td>${esc(r.branch)}</td>
        <td>${esc(r.condition)}</td>
        <td>${Number(r.quantity_on_hand).toLocaleString("en-IN")} ${esc(r.uom)}</td>
        <td>${money.format(r.stock_value)}</td>
        <td><span class="status ${cls}">${esc(r.status)}</span></td>
      </tr>`;
  }).join("");
}

function table(headers, rows) {
  return `
    <div class="table-wrap">
      <table>
        <thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

async function renderModule(view) {
  const box = qs("moduleView");
  box.innerHTML = `<section class="panel"><p class="muted">Loading...</p></section>`;
  if (view === "materials") return renderMaterials();
  if (view === "inward") return renderStockForm("INWARD");
  if (view === "outward") return renderStockForm("OUTWARD");
  if (view === "dispositions") return renderDispositions();
  if (view === "reports") return renderReports();
  if (view === "imports") return renderImports();
  if (view === "settings") return renderSettings();
}

async function renderMaterials() {
  const [materials, categories] = await Promise.all([loadMaterials(), api("/api/categories")]);
  qs("moduleView").innerHTML = `
    <section class="panel form-panel">
      <div class="panel-head"><h3>Add Material</h3></div>
      <form id="addMaterialForm" class="module-form">
        <label>Material ID / SKU <input name="sku" placeholder="Example: BAT-12V-100AH" required></label>
        <label>Item Name <input name="itemName" placeholder="Material name" required></label>
        <label>Description <input name="description" placeholder="Optional description"></label>
        <label>From Location / Supplier <input name="sourceLocation" placeholder="Example: Vendor, customer, old site"></label>
        <label>To Branch
          <select name="destinationBranchId" required>
            ${branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}
          </select>
        </label>
        <label>Category
          <select name="categoryId" required>
            ${categories.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}
          </select>
        </label>
        <label>UOM <input name="uom" value="PCS" required></label>
        <label>Minimum Stock Level <input name="minimumStockLevel" type="number" min="0" step="0.001" value="0" required></label>
        <label>Opening Quantity <input name="openingQuantity" type="number" min="0" step="0.001" value="0" required></label>
        <label>Standard Unit Price <input name="standardUnitPrice" type="number" min="0" step="0.01" value="0" required></label>
        <button type="submit">Add Material</button>
      </form>
    </section>
    <section class="panel">
      <div class="panel-head">
        <h3>Material Master</h3>
        <input id="materialSearch" class="search" placeholder="Search SKU or item">
      </div>
      ${table(["ID", "SKU", "Item", "From", "To Branch", "Category", "Good Qty", "Total Qty", "Value"], materialRows(materials))}
    </section>`;
  qs("materialSearch").addEventListener("input", (e) => {
    const term = e.target.value.toLowerCase();
    const filtered = materials.filter((m) => `${m.sku} ${m.item_name}`.toLowerCase().includes(term));
    qs("moduleView").querySelector("tbody").innerHTML = materialRows(filtered);
  });
  qs("addMaterialForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    try {
      const res = await api("/api/materials", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(form.entries())),
      });
      showMessage(`Material added: ${res.sku}`);
      await renderMaterials();
    } catch (err) {
      showMessage(err?.error?.message || "Could not add material", true);
    }
  });
}

function materialRows(materials) {
  return materials.map((m) => `
    <tr>
      <td>${m.id}</td>
      <td><strong>${esc(m.sku)}</strong></td>
      <td>${esc(m.item_name)}</td>
      <td>${esc(m.source_location || "")}</td>
      <td>${esc(m.destination_branch || "")}</td>
      <td>${esc(m.category)}</td>
      <td>${Number(m.good_qty).toLocaleString("en-IN")}</td>
      <td>${Number(m.total_qty).toLocaleString("en-IN")}</td>
      <td>${money.format(m.total_value)}</td>
    </tr>`).join("");
}

async function renderStockForm(type) {
  await loadMaterials();
  const isInward = type === "INWARD";
  const title = isInward ? "Receive Inward Stock" : "Issue Outward Stock";
  qs("moduleView").innerHTML = `
    <section class="panel form-panel">
      <h3>${title}</h3>
      <form id="moduleStockForm" class="module-form">
        <label>Branch
          <select name="branchId">
            ${branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}
          </select>
        </label>
        <label>Material
          <select name="materialId">${materialOptions()}</select>
        </label>
        <label>Quantity <input name="quantity" type="number" min="0.001" step="0.001" required></label>
        ${isInward ? `
        <label>Unit Price <input name="unitPrice" type="number" min="0" step="0.01" required></label>
        <label>Condition
          <select name="condition">
            <option>GOOD</option><option>REJECTED</option><option>DAMAGED</option>
            <option>BUYBACK</option><option>SCRAP</option>
          </select>
        </label>` : ""}
        <label>Reference No. <input name="referenceNo" placeholder="${isInward ? "PO number" : "Requisition number"}"></label>
        <label>Remarks <input name="remarks" placeholder="Optional note"></label>
        <div id="moduleFormMsg" class="message" style="min-height:1.4em"></div>
        <button type="submit" id="moduleStockBtn">${isInward ? "Save Inward" : "Save Outward"}</button>
      </form>
    </section>
    <section class="panel">
      <h3>${isInward ? "Recent Inward Entries" : "Recent Outward Entries"}</h3>
      <div id="transactionList"></div>
    </section>`;
  qs("moduleStockForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = qs("moduleStockBtn");
    const msgEl = qs("moduleFormMsg");
    btn.disabled = true;
    btn.textContent = "Saving…";
    msgEl.textContent = "";
    msgEl.style.color = "";
    const form = new FormData(e.currentTarget);
    const payload = Object.fromEntries(form.entries());

    // ── Step 1: save ──────────────────────────────────────────
    let saveOk = false;
    let txNo = "";
    try {
      const res = await api(isInward ? "/api/stock/inward" : "/api/stock/outward", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      txNo = res.transactionNo || res.transaction_no || "saved";
      saveOk = true;
    } catch (err) {
      // Extract a human-readable message; never show raw {}
      let msg = "Could not save transaction";
      if (err?.error?.message) msg = err.error.message;
      else if (typeof err === "string" && err.trim()) msg = err;
      else if (err?.message) msg = err.message;
      msgEl.textContent = `✗ ${msg}`;
      msgEl.style.color = "red";
      showMessage(msg, true);
    } finally {
      btn.disabled = false;
      btn.textContent = isInward ? "Save Inward" : "Save Outward";
    }

    if (!saveOk) return;

    // ── Step 2: show success and refresh list (independently) ─
    msgEl.textContent = `✓ Saved — ${txNo}`;
    msgEl.style.color = "green";
    showMessage(`Saved ${txNo}`);
    e.currentTarget.reset();
    try {
      await renderTransactions(type);
    } catch (_) {
      // list refresh failing doesn't undo the save
    }
  });
  await renderTransactions(type);
}

async function renderTransactions(type = "ALL") {
  const rows = await api(`/api/transactions?type=${type}`);
  const html = table(
    ["Txn No", "Type", "Branch", "Reference", "Date", "Qty", "Value", "Created By"],
    rows.map((r) => `
      <tr>
        <td><strong>${esc(r.transaction_no)}</strong></td>
        <td>${esc(r.transaction_type)}</td>
        <td>${esc(r.branch)}</td>
        <td>${esc(r.reference_no || "")}</td>
        <td>${esc(r.transaction_date)}</td>
        <td>${Number(r.total_quantity).toLocaleString("en-IN")}</td>
        <td>${money.format(r.total_value)}</td>
        <td>${esc(r.created_by)}</td>
      </tr>`).join("")
  );
  const target = qs("transactionList");
  if (target) target.innerHTML = html;
  return html;
}

async function renderDispositions() {
  await loadMaterials();
  const inventory = await api("/api/inventory?condition=ALL");
  const nonGood = inventory.filter((r) => r.condition !== "GOOD");
  qs("moduleView").innerHTML = `
    <section class="panel form-panel">
      <h3>Move GOOD Stock to Disposition</h3>
      <form id="dispositionForm" class="module-form">
        <label>Branch <select name="branchId">${branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}</select></label>
        <label>Material <select name="materialId">${materialOptions()}</select></label>
        <label>Quantity <input name="quantity" type="number" min="0.001" step="0.001" required></label>
        <label>To Condition
          <select name="toCondition">
            <option>DAMAGED</option><option>SCRAP</option><option>REJECTED</option><option>BUYBACK</option>
          </select>
        </label>
        <label>Remarks <input name="remarks" placeholder="Reason"></label>
        <button type="submit">Move Stock</button>
      </form>
    </section>
    <section class="panel">
      <h3>Disposition Ledger</h3>
      ${table(["SKU", "Item", "Branch", "Condition", "Qty", "Value"], nonGood.map((r) => `
        <tr>
          <td>${esc(r.sku)}</td><td>${esc(r.item_name)}</td><td>${esc(r.branch)}</td>
          <td>${esc(r.condition)}</td><td>${Number(r.quantity_on_hand).toLocaleString("en-IN")}</td>
          <td>${money.format(r.stock_value)}</td>
        </tr>`).join(""))}
    </section>`;
  qs("dispositionForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await api("/api/stock/disposition", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(new FormData(e.currentTarget).entries())),
      });
      showMessage(`Saved ${res.transactionNo}`);
      await renderDispositions();
    } catch (err) {
      showMessage(err?.error?.message || "Could not move stock", true);
    }
  });
}

async function renderReports() {
  const branchId = qs("branchSelect").value || "all";
  const condition = qs("conditionSelect").value || "ALL";
  const rows = await api(`/api/reports/stock?branchId=${branchId}&condition=${condition}`);
  qs("moduleView").innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <h3>Stock Report</h3>
        <button id="exportStockBtn" class="button-link" type="button">Export CSV</button>
      </div>
      ${table(["Branch", "Condition", "Category", "Items", "Quantity", "Value"], rows.map((r) => `
        <tr>
          <td>${esc(r.branch)}</td><td>${esc(r.condition)}</td><td>${esc(r.category)}</td>
          <td>${r.item_count}</td><td>${Number(r.quantity).toLocaleString("en-IN")}</td>
          <td>${money.format(r.value)}</td>
        </tr>`).join(""))}
    </section>`;
  qs("exportStockBtn").addEventListener("click", () => downloadStockCsv(branchId, condition));
}

async function downloadStockCsv(branchId, condition) {
  const res = await fetch(`/api/exports/stock.csv?branchId=${branchId}&condition=${condition}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) return showMessage("Export failed", true);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "prostarm-active-stock.csv";
  link.click();
  URL.revokeObjectURL(url);
}

async function renderImports() {
  const activity = await api("/api/activity");
  qs("moduleView").innerHTML = `
    <section class="panel">
      <h3>Excel Import</h3>
      <p class="muted">Book1.xlsx has been imported into the local database as opening Mahape godown stock.</p>
      <div class="summary-grid">
        <article><span>Source File</span><strong>Book1.xlsx</strong></article>
        <article><span>Status</span><strong>Imported</strong></article>
      </div>
    </section>
    <section class="panel">
      <h3>Import Activity</h3>
      ${table(["Txn No", "Type", "Branch", "Reference", "Date"], activity.map((r) => `
        <tr>
          <td>${esc(r.transaction_no)}</td><td>${esc(r.transaction_type)}</td>
          <td>${esc(r.branch)}</td><td>${esc(r.reference_no)}</td>
          <td>${esc(r.transaction_date)}</td>
        </tr>`).join(""))}
    </section>`;
}

async function renderSettings() {
  const [categories, users] = await Promise.all([
    api("/api/categories"),
    currentUser?.role === "ADMIN" ? api("/api/users") : Promise.resolve([]),
  ]);
  qs("moduleView").innerHTML = `
    <section class="grid">
      <article class="panel">
        <h3>Branches</h3>
        ${table(["Code", "Name", "Type"], branches.map((b) => `<tr><td>${esc(b.code)}</td><td>${esc(b.name)}</td><td>${esc(b.type)}</td></tr>`).join(""))}
      </article>
      <article class="panel">
        <h3>Categories</h3>
        ${table(["ID", "Name"], categories.map((c) => `<tr><td>${c.id}</td><td>${esc(c.name)}</td></tr>`).join(""))}
      </article>
    </section>
    <section class="panel">
      <h3>Users</h3>
      ${currentUser?.role === "ADMIN"
        ? table(["Name", "Email", "Role"], users.map((u) => `<tr><td>${esc(u.full_name)}</td><td>${esc(u.email)}</td><td>${esc(u.role)}</td></tr>`).join(""))
        : '<p class="muted">Admin access required to view users.</p>'}
    </section>`;
}

async function boot() {
  if (!token) return showLogin();
  try {
    const me = await api("/api/auth/me");
    currentUser = me.user;
    qs("userBadge").textContent = `${currentUser.name} - ${currentUser.role}`;
    await loadBranches();
    await loadMaterials();
    await setView(activeView);
    showApp();
  } catch {
    localStorage.removeItem("prostarm_token");
    token = null;
    showLogin();
  }
}

// ── Login ──────────────────────────────────────────────────────────────────
qs("loginBtn").addEventListener("click", async () => {
  const errEl = qs("loginError");
  errEl.textContent = "";
  const form = new FormData(qs("loginForm"));
  try {
    const res = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
    });
    token = res.token;
    localStorage.setItem("prostarm_token", token);
    await boot();
  } catch (err) {
    errEl.textContent = err?.error?.message || "Login failed";
  }
});

// ── Nav ────────────────────────────────────────────────────────────────────
document.querySelectorAll("nav a[data-view]").forEach((link) => {
  link.addEventListener("click", () => setView(link.dataset.view));
});

qs("branchSelect").addEventListener("change", () =>
  activeView === "dashboard" ? loadDashboard() : renderModule(activeView)
);
qs("conditionSelect").addEventListener("change", () =>
  activeView === "dashboard" ? loadDashboard() : renderModule(activeView)
);

// ── Dashboard quick-entry form ─────────────────────────────────────────────
qs("stockForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msgEl = qs("stockMessage");
  msgEl.textContent = "";
  msgEl.style.color = "";
  const form = new FormData(e.currentTarget);
  const type = form.get("type");
  let saveOk = false;
  try {
    const res = await api(type === "INWARD" ? "/api/stock/inward" : "/api/stock/outward", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    const txNo = res.transactionNo || res.transaction_no || "saved";
    msgEl.textContent = `✓ Saved — ${txNo}`;
    msgEl.style.color = "green";
    saveOk = true;
  } catch (err) {
    let msg = "Could not save transaction";
    if (err?.error?.message) msg = err.error.message;
    else if (typeof err === "string" && err.trim()) msg = err;
    else if (err?.message) msg = err.message;
    msgEl.textContent = `✗ ${msg}`;
    msgEl.style.color = "red";
  }
  if (saveOk) {
    try { await loadDashboard(); } catch (_) {}
  }
});

boot();
