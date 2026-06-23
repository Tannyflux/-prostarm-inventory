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

function showMessage(text, isError = false) {
  const el = qs("pageMessage");
  el.textContent = text;
  el.className = `page-message ${isError ? "error-box" : "success-box"}`;
  setTimeout(() => el.classList.add("hidden"), 3500);
}

function showApp() {
  qs("login").classList.add("hidden");
  qs("app").classList.remove("hidden");
}

function showLogin() {
  qs("app").classList.add("hidden");
  qs("login").classList.remove("hidden");
}

/* ---------- Modal helpers ---------- */
function openModal(title, bodyHtml) {
  qs("modalTitle").textContent = title;
  qs("modalBody").innerHTML = bodyHtml;
  qs("modalRoot").classList.remove("hidden");
  document.body.classList.add("modal-open");
  return qs("modalBody");
}

function closeModal() {
  qs("modalRoot").classList.add("hidden");
  qs("modalBody").innerHTML = "";
  document.body.classList.remove("modal-open");
}

/* ---------- Sidebar (hamburger) ---------- */
function setSidebar(open) {
  qs("sidebar").classList.toggle("open", open);
  qs("sidebarBackdrop").classList.toggle("hidden", !open);
  qs("menuToggle").setAttribute("aria-expanded", String(open));
}

function toggleSidebar() {
  setSidebar(!qs("sidebar").classList.contains("open"));
}

/* ---------- Data loaders ---------- */
async function loadBranches() {
  branches = await api("/api/branches");
  const options = [`<option value="all">All Locations</option>`].concat(
    branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`)
  ).join("");
  qs("branchSelect").innerHTML = options;
}

async function loadMaterials() {
  materialCache = await api("/api/materials");
  return materialCache;
}

function materialOptions() {
  return materialCache.map((m) => `<option value="${m.id}">${esc(m.item_name)} (${esc(m.sku)})</option>`).join("");
}

function branchOptions() {
  return branches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("");
}

/* ---------- Views ---------- */
async function setView(view) {
  activeView = view;
  qs("pageTitle").textContent = titles[view];
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  qs("dashboardView").classList.toggle("hidden", view !== "dashboard");
  qs("moduleView").classList.toggle("hidden", view === "dashboard");
  setSidebar(false);
  if (view === "dashboard") return loadDashboard();
  await renderModule(view);
}

async function loadDashboard() {
  const branchId = qs("branchSelect").value || "all";
  const condition = qs("conditionSelect").value || "ALL";
  const [summary, inventory, activity] = await Promise.all([
    api(`/api/dashboard?branchId=${branchId}`),
    api(`/api/inventory?branchId=${branchId}&condition=${condition}`),
    api("/api/activity"),
  ]);
  inventoryCache = inventory;
  qs("totalItems").textContent = summary.totalItems;
  qs("lowStockAlerts").textContent = summary.lowStockAlerts;
  qs("totalValuation").textContent = money.format(summary.totalValuation);
  qs("recentActivity").textContent = summary.recentActivity;
  renderBars(summary.byCondition);
  renderInventory(inventory);
  renderActivity(activity);
}

function renderBars(rows) {
  if (!rows.length) {
    qs("conditionBars").innerHTML = `<p class="muted small">No stock recorded yet. Import your stock sheet from the Imports tab.</p>`;
    return;
  }
  const max = Math.max(1, ...rows.map((r) => Number(r.quantity)));
  qs("conditionBars").innerHTML = rows.map((r) => {
    const width = Math.round((Number(r.quantity) / max) * 100);
    return `
      <div class="bar-row">
        <strong>${esc(r.condition)}</strong>
        <span class="bar-track"><span class="bar-fill" style="width:${width}%"></span></span>
        <span>${Number(r.quantity).toLocaleString("en-IN")}</span>
      </div>`;
  }).join("");
}

function renderActivity(rows) {
  const el = qs("recentActivityList");
  if (!el) return;
  if (!rows.length) {
    el.innerHTML = `<p class="muted small">No recent activity.</p>`;
    return;
  }
  el.innerHTML = rows.map((r) => `
    <div class="activity-item">
      <div>
        <strong>${esc(r.transaction_no)}</strong>
        <span class="muted small">${esc(r.branch)} &middot; ${esc(r.transaction_date)}</span>
      </div>
      <span class="tag tag-${esc((r.transaction_type || "").toLowerCase())}">${esc(r.transaction_type)}</span>
    </div>`).join("");
}

function renderInventory(rows) {
  if (!rows.length) {
    qs("inventoryRows").innerHTML = `<tr><td colspan="9" class="empty-cell">No stock to display. Use the Imports tab to upload your stock sheet.</td></tr>`;
    return;
  }
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
        <tbody>${rows || `<tr><td colspan="${headers.length}" class="empty-cell">No records yet.</td></tr>`}</tbody>
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

/* ---------- Material Master ---------- */
let categoryCache = [];
async function renderMaterials() {
  const [materials, categories] = await Promise.all([loadMaterials(), api("/api/categories")]);
  categoryCache = categories;
  qs("moduleView").innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <h3>Material Master</h3>
        <div class="head-actions">
          <input id="materialSearch" class="search" placeholder="Search SKU or item">
          <button id="openAddMaterial" type="button">Add Material</button>
        </div>
      </div>
      ${table(["ID", "SKU", "Item", "From", "To Branch", "Category", "Good Qty", "Total Qty", "Value"], materialRows(materials))}
    </section>`;
  qs("materialSearch").addEventListener("input", (event) => {
    const term = event.target.value.toLowerCase();
    const filtered = materials.filter((m) => `${m.sku} ${m.item_name}`.toLowerCase().includes(term));
    qs("moduleView").querySelector("tbody").innerHTML = materialRows(filtered) || `<tr><td colspan="9" class="empty-cell">No matches.</td></tr>`;
  });
  qs("openAddMaterial").addEventListener("click", openMaterialModal);
}

function openMaterialModal() {
  const body = openModal("Add Material", `
    <form id="addMaterialForm" class="module-form">
      <label>Material ID / SKU <input name="sku" placeholder="Example: BAT-12V-100AH" required></label>
      <label>Item Name <input name="itemName" placeholder="Material name" required></label>
      <label class="span-2">Description <input name="description" placeholder="Optional description"></label>
      <label>From Location / Supplier <input name="sourceLocation" placeholder="Vendor, customer, old site"></label>
      <label>To Branch
        <select name="destinationBranchId" required>${branchOptions()}</select>
      </label>
      <label>Category
        <select name="categoryId" required>${categoryCache.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select>
      </label>
      <label>UOM <input name="uom" value="PCS" required></label>
      <label>Minimum Stock Level <input name="minimumStockLevel" type="number" min="0" step="0.001" value="0" required></label>
      <label>Opening Quantity <input name="openingQuantity" type="number" min="0" step="0.001" value="0" required></label>
      <label class="span-2">Standard Unit Price <input name="standardUnitPrice" type="number" min="0" step="0.01" value="0" required></label>
      <div class="modal-actions span-2">
        <button type="button" class="ghost" data-close>Cancel</button>
        <button type="submit">Add Material</button>
      </div>
    </form>`);
  body.querySelector("[data-close]").addEventListener("click", closeModal);
  body.querySelector("#addMaterialForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const res = await api("/api/materials", { method: "POST", body: JSON.stringify(Object.fromEntries(form.entries())) });
      closeModal();
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

/* ---------- Inward / Outward ---------- */
async function renderStockForm(type) {
  await loadMaterials();
  const isInward = type === "INWARD";
  const title = isInward ? "Receive Inward Stock" : "Issue Outward Stock";
  qs("moduleView").innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <h3>${isInward ? "Inward Stock" : "Outward Stock"}</h3>
        <button id="openStockModal" type="button">${title}</button>
      </div>
      <div id="transactionList"></div>
    </section>`;
  qs("openStockModal").addEventListener("click", () => openStockModal(type));
  await renderTransactions(type);
}

function openStockModal(type) {
  const isInward = type === "INWARD";
  const title = isInward ? "Receive Inward Stock" : "Issue Outward Stock";
  const body = openModal(title, `
    <form id="moduleStockForm" class="module-form">
      <label>Branch <select name="branchId">${branchOptions()}</select></label>
      <label>Material <select name="materialId">${materialOptions()}</select></label>
      <label>Quantity <input name="quantity" type="number" min="0.001" step="0.001" required></label>
      ${isInward ? `
        <label>Unit Price <input name="unitPrice" type="number" min="0" step="0.01" required></label>
        <label>Condition <select name="condition"><option>GOOD</option><option>REJECTED</option><option>DAMAGED</option><option>BUYBACK</option><option>SCRAP</option></select></label>` : ""}
      <label>Reference No. <input name="referenceNo" placeholder="${isInward ? "PO number" : "Requisition number"}"></label>
      <label class="span-2">Remarks <input name="remarks" placeholder="Optional note"></label>
      <div class="modal-actions span-2">
        <button type="button" class="ghost" data-close>Cancel</button>
        <button type="submit">${isInward ? "Save Inward" : "Save Outward"}</button>
      </div>
    </form>`);
  body.querySelector("[data-close]").addEventListener("click", closeModal);
  body.querySelector("#moduleStockForm").addEventListener("submit", (event) => submitStockForm(event, type));
}

async function submitStockForm(event, type) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  try {
    const res = await api(type === "INWARD" ? "/api/stock/inward" : "/api/stock/outward", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    closeModal();
    showMessage(`Saved ${res.transactionNo}`);
    await renderTransactions(type);
  } catch (err) {
    showMessage(err?.error?.message || "Could not save transaction", true);
  }
}

async function renderTransactions(type = "ALL") {
  const rows = await api(`/api/transactions?type=${type}`);
  const html = table(["Txn No", "Type", "Branch", "Reference", "Date", "Qty", "Value", "Created By"], rows.map((r) => `
    <tr>
      <td><strong>${esc(r.transaction_no)}</strong></td>
      <td>${esc(r.transaction_type)}</td>
      <td>${esc(r.branch)}</td>
      <td>${esc(r.reference_no || "")}</td>
      <td>${esc(r.transaction_date)}</td>
      <td>${Number(r.total_quantity).toLocaleString("en-IN")}</td>
      <td>${money.format(r.total_value)}</td>
      <td>${esc(r.created_by)}</td>
    </tr>`).join(""));
  const target = qs("transactionList");
  if (target) target.innerHTML = html;
  return html;
}

/* ---------- Dispositions ---------- */
async function renderDispositions() {
  await loadMaterials();
  const inventory = await api("/api/inventory?condition=ALL");
  const nonGood = inventory.filter((r) => r.condition !== "GOOD");
  qs("moduleView").innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <h3>Disposition Ledger</h3>
        <button id="openDisposition" type="button">Move GOOD Stock to Disposition</button>
      </div>
      ${table(["SKU", "Item", "Branch", "Condition", "Qty", "Value"], nonGood.map((r) => `
        <tr>
          <td>${esc(r.sku)}</td><td>${esc(r.item_name)}</td><td>${esc(r.branch)}</td>
          <td>${esc(r.condition)}</td><td>${Number(r.quantity_on_hand).toLocaleString("en-IN")}</td><td>${money.format(r.stock_value)}</td>
        </tr>`).join(""))}
    </section>`;
  qs("openDisposition").addEventListener("click", openDispositionModal);
}

function openDispositionModal() {
  const body = openModal("Move GOOD Stock to Disposition", `
    <form id="dispositionForm" class="module-form">
      <label>Branch <select name="branchId">${branchOptions()}</select></label>
      <label>Material <select name="materialId">${materialOptions()}</select></label>
      <label>Quantity <input name="quantity" type="number" min="0.001" step="0.001" required></label>
      <label>To Condition <select name="toCondition"><option>DAMAGED</option><option>SCRAP</option><option>REJECTED</option><option>BUYBACK</option></select></label>
      <label class="span-2">Remarks <input name="remarks" placeholder="Reason"></label>
      <div class="modal-actions span-2">
        <button type="button" class="ghost" data-close>Cancel</button>
        <button type="submit">Move Stock</button>
      </div>
    </form>`);
  body.querySelector("[data-close]").addEventListener("click", closeModal);
  body.querySelector("#dispositionForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const res = await api("/api/stock/disposition", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget).entries())) });
      closeModal();
      showMessage(`Saved ${res.transactionNo}`);
      await renderDispositions();
    } catch (err) {
      showMessage(err?.error?.message || "Could not move stock", true);
    }
  });
}

/* ---------- Reports ---------- */
async function renderReports() {
  const branchId = qs("branchSelect").value || "all";
  const condition = qs("conditionSelect").value || "ALL";
  const rows = await api(`/api/reports/stock?branchId=${branchId}&condition=${condition}`);
  qs("moduleView").innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <h3>Stock Report</h3>
        <button id="exportStockBtn" type="button">Export CSV</button>
      </div>
      ${table(["Branch", "Condition", "Category", "Items", "Quantity", "Value"], rows.map((r) => `
        <tr>
          <td>${esc(r.branch)}</td><td>${esc(r.condition)}</td><td>${esc(r.category)}</td>
          <td>${r.item_count}</td><td>${Number(r.quantity).toLocaleString("en-IN")}</td><td>${money.format(r.value)}</td>
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

/* ---------- Imports ---------- */
async function renderImports() {
  const activity = await api("/api/activity");
  qs("moduleView").innerHTML = `
    <section class="panel form-panel">
      <div class="panel-head"><h3>Import Stock from Excel / CSV</h3></div>
      <p class="muted small">Upload your stock sheet. Expected columns in order: <strong>Item Name</strong>, <strong>Quantity</strong>, <strong>Rate</strong>, <strong>Value</strong>. Conditions (damaged, rejected, scrap, buyback) are detected from the item name.</p>
      <form id="importForm" class="module-form">
        <label>Target Branch <select name="branchId">${branchOptions()}</select></label>
        <label>Import Mode
          <select name="replace">
            <option value="true">Replace all existing stock</option>
            <option value="false">Add to existing stock</option>
          </select>
        </label>
        <label class="span-2">Stock File (.xlsx or .csv) <input id="importFile" name="file" type="file" accept=".xlsx,.csv" required></label>
        <div class="modal-actions span-2">
          <button type="submit">Upload &amp; Import</button>
        </div>
      </form>
      <p id="importStatus" class="message"></p>
    </section>
    <section class="panel">
      <div class="panel-head"><h3>Import Activity</h3></div>
      ${table(["Txn No", "Type", "Branch", "Reference", "Date"], activity.map((r) => `
        <tr><td>${esc(r.transaction_no)}</td><td>${esc(r.transaction_type)}</td><td>${esc(r.branch)}</td><td>${esc(r.reference_no || "")}</td><td>${esc(r.transaction_date)}</td></tr>`).join(""))}
    </section>`;
  qs("importForm").addEventListener("submit", submitImport);
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",").pop());
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function submitImport(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const file = qs("importFile").files[0];
  if (!file) return showMessage("Choose a file first", true);
  const data = new FormData(form);
  qs("importStatus").textContent = "Uploading and importing...";
  try {
    const contentBase64 = await readFileAsBase64(file);
    const res = await api("/api/imports/stock", {
      method: "POST",
      body: JSON.stringify({
        fileName: file.name,
        contentBase64,
        branchId: data.get("branchId"),
        replace: data.get("replace") === "true",
      }),
    });
    qs("importStatus").textContent = `Imported ${res.imported} items into ${res.branch}.`;
    showMessage(`Imported ${res.imported} items`);
    await renderImports();
  } catch (err) {
    qs("importStatus").textContent = "";
    showMessage(err?.error?.message || "Import failed", true);
  }
}

/* ---------- Settings ---------- */
async function renderSettings() {
  const categories = await api("/api/categories");
  qs("moduleView").innerHTML = `
    <section class="grid">
      <article class="panel">
        <div class="panel-head"><h3>Branches</h3></div>
        ${table(["Code", "Name", "Type"], branches.map((b) => `<tr><td>${esc(b.code)}</td><td>${esc(b.name)}</td><td>${esc(b.type)}</td></tr>`).join(""))}
      </article>
      <article class="panel">
        <div class="panel-head"><h3>Categories</h3></div>
        ${table(["ID", "Name"], categories.map((c) => `<tr><td>${c.id}</td><td>${esc(c.name)}</td></tr>`).join(""))}
      </article>
    </section>`;
}

/* ---------- Boot ---------- */
async function boot() {
  if (!token) return showLogin();
  try {
    const me = await api("/api/auth/me");
    currentUser = me.user;
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

qs("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  qs("loginError").textContent = "";
  const form = new FormData(event.currentTarget);
  try {
    const res = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
    });
    token = res.token;
    localStorage.setItem("prostarm_token", token);
    await boot();
  } catch (err) {
    qs("loginError").textContent = err?.error?.message || "Login failed";
  }
});

qs("logoutBtn").addEventListener("click", () => {
  localStorage.removeItem("prostarm_token");
  token = null;
  showLogin();
});

document.querySelectorAll("nav a[data-view]").forEach((link) => {
  link.addEventListener("click", () => setView(link.dataset.view));
});

qs("menuToggle").addEventListener("click", toggleSidebar);
qs("sidebarBackdrop").addEventListener("click", () => setSidebar(false));
qs("modalClose").addEventListener("click", closeModal);
qs("modalBackdrop").addEventListener("click", closeModal);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !qs("modalRoot").classList.contains("hidden")) closeModal();
});

qs("branchSelect").addEventListener("change", () => activeView === "dashboard" ? loadDashboard() : renderModule(activeView));
qs("conditionSelect").addEventListener("change", () => activeView === "dashboard" ? loadDashboard() : renderModule(activeView));

boot();
