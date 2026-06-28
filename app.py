from __future__ import annotations

import base64
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import threading
import zipfile
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import psycopg2
from psycopg2.extras import DictCursor


ROOT = Path(__file__).resolve().parent
DB_PATH = Path("/tmp/prostarm_inventory.db")
STATIC_DIR = ROOT / "static"
WORKBOOK_PATH = ROOT / "Book1.xlsx"
SECRET = os.environ.get("PROSTARM_SECRET", "change-this-secret-before-production")
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


DATABASE_URL = os.environ.get("DATABASE_URL")

def db():
    """
    Dynamically connects to Neon (PostgreSQL) if DATABASE_URL is available.
    Falls back to a local SQLite file during local development if needed.
    """
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
        return PostgresToSQLiteAdapter(conn)
    else:
        conn = sqlite3.connect("/tmp/prostarm_inventory.db")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

class PostgresToSQLiteAdapter:
    """Translates SQLite-style python calls into PostgreSQL-compatible execution."""
    def __init__(self, pg_conn):
        self.pg_conn = pg_conn
        self._cursor = None

    def execute(self, query, params=None):
        query = query.replace("?", "%s")
        self._cursor = self.pg_conn.cursor()
        self._cursor.execute(query, params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return row if row is not None else None

    def fetchall(self):
        return self._cursor.fetchall()
        
    def executemany(self, query, params_seq):
        query = query.replace("?", "%s")
        self._cursor = self.pg_conn.cursor()
        self._cursor.executemany(query, params_seq)
        return self

    def executescript(self, script):
        self._cursor = self.pg_conn.cursor()
        self._cursor.execute(script)
        return self

    def commit(self):
        self.pg_conn.commit()
        
    def rollback(self):
        self.pg_conn.rollback()

    def close(self):
        if self._cursor:
            self._cursor.close()
        self.pg_conn.close()
        
    def __enter__(self):
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 180_000)
    return base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    salt_b64, digest_b64 = encoded.split("$", 1)
    salt = base64.urlsafe_b64decode(salt_b64.encode())
    expected = base64.urlsafe_b64decode(digest_b64.encode())
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 180_000)
    return hmac.compare_digest(expected, actual)


def b64json(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign(data: str) -> str:
    digest = hmac.new(SECRET.encode(), data.encode(), hashlib.sha256).digest()
    "exp": int((dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).timestamp()),

def create_token(user: sqlite3.Row) -> str:
    header = b64json({"alg": "HS256", "typ": "JWT"})
    payload = b64json({
        "sub": user["id"],
        "email": user["email"],
        "role": user["role"],
        "name": user["full_name"],
        "exp": int((dt.datetime.now(dt.UTC) + dt.timedelta(hours=8)).timestamp()),
    })
    body = f"{header}.{payload}"
    return f"{body}.{sign(body)}"


def read_token(token: str) -> dict | None:
    try:
        header, payload, signature = token.split(".", 2)
        body = f"{header}.{payload}"
        if not hmac.compare_digest(signature, sign(body)):
            return None
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if data.get("exp", 0) < int(dt.timezone.utc).timestamp()):
            return None
        return data
    except Exception:
        return None


SCHEMA = """
CREATE TABLE IF NOT EXISTS branches (
  id SERIAL PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'WAREHOUSE'
);
CREATE TABLE IF NOT EXISTS users (
  id SERIAL PRIMARY KEY,
  full_name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS materials (
  id SERIAL PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE,
  item_name TEXT NOT NULL,
  description TEXT,
  source_location TEXT,
  destination_branch_id INTEGER REFERENCES branches(id),
  category_id INTEGER NOT NULL REFERENCES categories(id),
  uom TEXT NOT NULL,
  minimum_stock_level REAL NOT NULL DEFAULT 0,
  standard_unit_price REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS inventory_balances (
  id SERIAL PRIMARY KEY,
  material_id INTEGER NOT NULL REFERENCES materials(id),
  branch_id INTEGER NOT NULL REFERENCES branches(id),
  condition TEXT NOT NULL DEFAULT 'GOOD',
  quantity_on_hand REAL NOT NULL DEFAULT 0,
  average_unit_cost REAL NOT NULL DEFAULT 0,
  UNIQUE(material_id, branch_id, condition)
);
CREATE TABLE IF NOT EXISTS stock_transactions (
  id SERIAL PRIMARY KEY,
  transaction_no TEXT NOT NULL UNIQUE,
  transaction_type TEXT NOT NULL,
  branch_id INTEGER NOT NULL REFERENCES branches(id),
  reference_no TEXT,
  counterparty_name TEXT,
  department_or_client TEXT,
  transaction_date TEXT NOT NULL,
  remarks TEXT,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stock_transaction_lines (
  id SERIAL PRIMARY KEY,
  transaction_id INTEGER NOT NULL REFERENCES stock_transactions(id),
  material_id INTEGER NOT NULL REFERENCES materials(id),
  quantity REAL NOT NULL,
  unit_price REAL NOT NULL DEFAULT 0,
  condition_from TEXT,
  condition_to TEXT
);
"""
def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_branch(conn: sqlite3.Connection, code: str, name: str, branch_type: str = "BRANCH") -> int:
    row = conn.execute("SELECT id FROM branches WHERE code = ?", (code,)).fetchone()
    if row:
        return int(row["id"])
    return int(conn.execute(
        "INSERT INTO branches(code, name, type) VALUES (?, ?, ?) RETURNING id",
        (code, name, branch_type),
    ).fetchone()[0])

def migrate(conn) -> None:
    ensure_branch(conn, "MAHAPE", "Mahape", "WAREHOUSE")
    ensure_branch(conn, "PUNE", "Pune", "BRANCH")
    ensure_branch(conn, "AHMEDABAD", "Ahmedabad", "BRANCH")


def cell_col(cell_ref: str) -> str:
    return "".join(ch for ch in cell_ref if ch.isalpha())


def parse_number(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_xlsx_rows(path: Path) -> list[dict[str, object]]:
    ns = {
        "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pkgrel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    with zipfile.ZipFile(path) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("main:si", ns):
                shared.append("".join(t.text or "" for t in si.findall(".//main:t", ns)))

        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels.findall("pkgrel:Relationship", ns)}
        first_sheet = workbook.find("main:sheets/main:sheet", ns)
        if first_sheet is None:
            return []
        rel_id = first_sheet.attrib[f"{{{ns['rel']}}}id"]
        target = rel_map[rel_id].lstrip("/")
        sheet_path = target if target.startswith("xl/") else f"xl/{target}"
        sheet = ET.fromstring(zf.read(sheet_path))

    rows: list[dict[str, object]] = []
    for row in sheet.findall("main:sheetData/main:row", ns):
        record: dict[str, object] = {}
        for cell in row.findall("main:c", ns):
            ref = cell.attrib.get("r", "")
            col = cell_col(ref)
            value = ""
            if cell.attrib.get("t") == "inlineStr":
                value = "".join(t.text or "" for t in cell.findall(".//main:t", ns))
            else:
                v = cell.find("main:v", ns)
                if v is not None and v.text is not None:
                    value = shared[int(v.text)] if cell.attrib.get("t") == "s" else v.text
            record[col] = value
        rows.append(record)
    return rows


def import_book1_inventory(conn: sqlite3.Connection) -> bool:
    if not WORKBOOK_PATH.exists():
        return False

    rows = load_xlsx_rows(WORKBOOK_PATH)
    branch_name = "Mahape"
    for row in rows:
        if row.get("B") and str(row.get("B")).strip().lower() == "mahape":
            branch_name = str(row.get("B")).strip()
            break

    branch_id = ensure_branch(conn, "MAHAPE", branch_name, "WAREHOUSE")

    category_id = conn.execute("INSERT INTO categories(name) VALUES (?) RETURNING id", ("Imported Godown Stock",)).fetchone()[0]
    imported = 0
    seen: dict[str, int] = {}
    for index, row in enumerate(rows, start=1):
        item_name = str(row.get("A") or "").replace("_x000D_", " ").strip()
        quantity = parse_number(row.get("B"))
        displayed_rate = parse_number(row.get("C")) or 0
        value = parse_number(row.get("D"))
        rate = (value / quantity) if value is not None and quantity else displayed_rate
        if not item_name or quantity is None or quantity <= 0:
            continue

        lowered = item_name.lower()
        condition = "GOOD"
        if "buyback" in lowered or "buy back" in lowered:
            condition = "BUYBACK"
        elif "reject" in lowered:
            condition = "REJECTED"
        elif "scrap" in lowered or "srap" in lowered:
            condition = "SCRAP"
        elif "damage" in lowered or "damaged" in lowered or "faulty" in lowered:
            condition = "DAMAGED"

        base_sku = "".join(ch if ch.isalnum() else "-" for ch in item_name.upper()).strip("-")
        base_sku = "-".join(part for part in base_sku.split("-") if part)[:42] or f"ITEM-{index}"
        seen[base_sku] = seen.get(base_sku, 0) + 1
        sku = base_sku if seen[base_sku] == 1 else f"{base_sku}-{seen[base_sku]}"

        material_id = conn.execute(
            """
            INSERT INTO materials(sku, item_name, description, source_location, destination_branch_id, category_id, uom, minimum_stock_level, standard_unit_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
            """,
            (sku, item_name, f"Imported from Book1.xlsx row {index}", "Book1.xlsx Godown Summary", branch_id, category_id, "PCS", 0, rate),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO inventory_balances(material_id, branch_id, condition, quantity_on_hand, average_unit_cost)
            VALUES (?, ?, ?, ?, ?)
            """,
            (material_id, branch_id, condition, quantity, rate),
        )
        imported += 1

    user_id = conn.execute("SELECT id FROM users WHERE email='admin@prostarm.com'").fetchone()[0]
    conn.execute(
        """
        INSERT INTO stock_transactions(
          transaction_no, transaction_type, branch_id, reference_no, counterparty_name,
          department_or_client, transaction_date, remarks, created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("TXN-BOOK1-IMPORT", "INWARD", branch_id, "BOOK1.XLSX", "Opening import", None, "2026-06-19", f"Imported {imported} stock rows from Book1.xlsx", user_id, now_iso()),
    )
    return imported > 0


def seed() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        migrate(conn)
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return

        conn.executemany(
            "INSERT INTO users(full_name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            [
                ("Admin User", "admin@prostarm.com", hash_password("Admin@12345"), "ADMIN"),
                ("Store Manager", "store@prostarm.com", hash_password("Store@12345"), "STORE_MANAGER"),
                ("Viewer User", "viewer@prostarm.com", hash_password("Viewer@12345"), "VIEWER"),
            ],
        )

        if import_book1_inventory(conn):
            return

        conn.executemany(
            "INSERT INTO branches(code, name, type) VALUES (?, ?, ?)",
            [
                ("MUM-WH", "Mumbai Warehouse", "WAREHOUSE"),
                ("DEL-BR", "Delhi Branch", "BRANCH"),
                ("BLR-SVC", "Bengaluru Service Center", "SERVICE_CENTER"),
            ],
        )
        conn.executemany(
            "INSERT INTO categories(name) VALUES (?)",
            [("Laptops",), ("Networking",), ("Storage",), ("Accessories",)],
        )

        cat = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}
        materials = [
            ("LAP-DELL-5420", "Dell Latitude 5420", "Business laptop", cat["Laptops"], "PCS", 5, 58000),
            ("RTR-CISCO-900", "Cisco ISR Router", "Branch router", cat["Networking"], "PCS", 3, 76000),
            ("SSD-1TB-NVME", "1TB NVMe SSD", "Replacement storage", cat["Storage"], "PCS", 10, 6200),
            ("KB-MOUSE-COMBO", "Keyboard Mouse Combo", "USB combo kit", cat["Accessories"], "PCS", 20, 850),
        ]
        conn.executemany(
            """
            INSERT INTO materials(sku, item_name, description, category_id, uom, minimum_stock_level, standard_unit_price)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            materials,
        )
        material_ids = {r["sku"]: r["id"] for r in conn.execute("SELECT id, sku FROM materials")}
        branch_ids = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM branches")}
        balances = [
            ("LAP-DELL-5420", "MUM-WH", "GOOD", 12, 58000),
            ("LAP-DELL-5420", "MUM-WH", "DAMAGED", 1, 42000),
            ("RTR-CISCO-900", "MUM-WH", "GOOD", 2, 76000),
            ("SSD-1TB-NVME", "MUM-WH", "GOOD", 35, 6200),
            ("KB-MOUSE-COMBO", "MUM-WH", "GOOD", 18, 850),
            ("LAP-DELL-5420", "DEL-BR", "GOOD", 4, 58000),
            ("RTR-CISCO-900", "DEL-BR", "REJECTED", 1, 76000),
            ("SSD-1TB-NVME", "BLR-SVC", "GOOD", 8, 6200),
            ("KB-MOUSE-COMBO", "BLR-SVC", "SCRAP", 3, 200),
            ("LAP-DELL-5420", "BLR-SVC", "BUYBACK", 2, 18000),
        ]
        conn.executemany(
            """
            INSERT INTO inventory_balances(material_id, branch_id, condition, quantity_on_hand, average_unit_cost)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(material_ids[s], branch_ids[b], c, q, p) for s, b, c, q, p in balances],
        )
        user_id = conn.execute("SELECT id FROM users WHERE email='admin@prostarm.com'").fetchone()[0]
        conn.execute(
            """
            INSERT INTO stock_transactions(
              transaction_no, transaction_type, branch_id, reference_no, counterparty_name,
              department_or_client, transaction_date, remarks, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("TXN-SEED-001", "INWARD", branch_ids["MUM-WH"], "PO-SEED", "Seed Supplier", None, "2026-06-19", "Demo opening stock", user_id, now_iso()),
        )


def rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


class App(BaseHTTPRequestHandler):
    server_version = "ProstarMIMS/1.0"

    def log_message(self, fmt: str, *args) -> None:
        return

    def send_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_csv(self, file_name: str, rows: list[dict]) -> None:
        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        body = output.getvalue().encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{file_name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode())

    def auth(self) -> dict | None:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        return read_token(header[7:])

    def require_auth(self) -> dict | None:
        user = self.auth()
        if not user:
            self.send_json(401, {"error": {"code": "UNAUTHORIZED", "message": "Login required"}})
            return None
        return user

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            return self.static_file("index.html")
        if path.startswith("/static/"):
            return self.static_file(path.removeprefix("/static/"))
        if path == "/api/health":
            return self.send_json(200, {"ok": True, "service": "ProstarM IMS", "time": now_iso()})

        user = self.require_auth()
        if not user:
            return

        if path == "/api/auth/me":
            return self.send_json(200, {"user": user})
        if path == "/api/branches":
            with db() as conn:
                return self.send_json(200, rows_to_dicts(conn.execute("SELECT * FROM branches ORDER BY name")))
        if path == "/api/categories":
            with db() as conn:
                return self.send_json(200, rows_to_dicts(conn.execute("SELECT * FROM categories ORDER BY name")))
        if path == "/api/users":
            if user["role"] != "ADMIN":
                return self.send_json(403, {"error": {"code": "FORBIDDEN", "message": "Admin access required"}})
            with db() as conn:
                return self.send_json(200, rows_to_dicts(conn.execute("SELECT id, full_name, email, role FROM users ORDER BY full_name")))
        if path == "/api/materials":
            return self.materials()
        if path == "/api/transactions":
            return self.transactions(query)
        if path == "/api/reports/stock":
            return self.report_stock(query)
        if path == "/api/exports/stock.csv":
            return self.export_stock(query)
        if path == "/api/inventory":
            return self.inventory(query)
        if path == "/api/dashboard":
            return self.dashboard(query)
        if path == "/api/activity":
            with db() as conn:
                rows = conn.execute(
                    """
                    SELECT st.transaction_no, st.transaction_type, b.name AS branch, st.reference_no,
                           st.transaction_date, st.created_at
                    FROM stock_transactions st
                    JOIN branches b ON b.id = st.branch_id
                    ORDER BY st.created_at DESC
                    LIMIT 10
                    """
                ).fetchall()
                return self.send_json(200, rows_to_dicts(rows))

        return self.send_json(404, {"error": {"code": "NOT_FOUND", "message": "Route not found"}})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/auth/login":
            data = self.read_json()
            with db() as conn:
                user = conn.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (data.get("email", ""),)).fetchone()
            if not user or not verify_password(data.get("password", ""), user["password_hash"]):
                return self.send_json(401, {"error": {"code": "INVALID_LOGIN", "message": "Invalid email or password"}})
            return self.send_json(200, {
                "token": create_token(user),
                "user": {"id": user["id"], "fullName": user["full_name"], "email": user["email"], "role": user["role"]},
            })

        user = self.require_auth()
        if not user:
            return
        if path == "/api/stock/inward":
            return self.stock_move("INWARD", user)
        if path == "/api/stock/outward":
            return self.stock_move("OUTWARD", user)
        if path == "/api/stock/disposition":
            return self.disposition(user)
        if path == "/api/materials":
            return self.create_material(user)
        if path == "/api/branches":
            return self.create_branch(user)
        return self.send_json(404, {"error": {"code": "NOT_FOUND", "message": "Route not found"}})

    def static_file(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists():
            self.send_error(404)
            return
        content_type = "text/html" if target.suffix == ".html" else "text/css" if target.suffix == ".css" else "application/javascript"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def inventory(self, query: dict) -> None:
        branch_id = query.get("branchId", ["all"])[0]
        condition = query.get("condition", ["ALL"])[0]
        clauses = []
        params = []
        if branch_id != "all":
            clauses.append("b.id = ?")
            params.append(branch_id)
        if condition != "ALL":
            clauses.append("ib.condition = ?")
            params.append(condition)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with db() as conn:
            rows = conn.execute(
                f"""
                SELECT ib.id, m.sku, m.item_name, c.name AS category, b.name AS branch,
                       b.id AS branch_id, ib.condition, ib.quantity_on_hand, m.uom,
                       m.minimum_stock_level, ib.average_unit_cost,
                       ROUND(ib.quantity_on_hand * ib.average_unit_cost, 2) AS stock_value,
                       CASE
                         WHEN ib.condition != 'GOOD' THEN 'Unavailable'
                         WHEN ib.quantity_on_hand <= m.minimum_stock_level THEN 'Low Stock'
                         ELSE 'Healthy'
                       END AS status
                FROM inventory_balances ib
                JOIN materials m ON m.id = ib.material_id
                JOIN categories c ON c.id = m.category_id
                JOIN branches b ON b.id = ib.branch_id
                {where}
                ORDER BY b.name, m.sku, ib.condition
                """,
                params,
            ).fetchall()
        self.send_json(200, rows_to_dicts(rows))

    def materials(self) -> None:
        with db() as conn:
            rows = conn.execute(
                """
                SELECT m.id, m.sku, m.item_name, m.description, m.source_location,
                       db.name AS destination_branch, db.id AS destination_branch_id,
                       c.name AS category, m.uom,
                       m.minimum_stock_level, m.standard_unit_price,
                       COALESCE(SUM(CASE WHEN ib.condition = 'GOOD' THEN ib.quantity_on_hand ELSE 0 END), 0) AS good_qty,
                       COALESCE(SUM(ib.quantity_on_hand), 0) AS total_qty,
                       COALESCE(SUM(ib.quantity_on_hand * ib.average_unit_cost), 0) AS total_value
                FROM materials m
                JOIN categories c ON c.id = m.category_id
                LEFT JOIN branches db ON db.id = m.destination_branch_id
                LEFT JOIN inventory_balances ib ON ib.material_id = m.id
                GROUP BY m.id, m.sku, m.item_name, m.description, m.source_location,
                         db.name, db.id, c.name, m.uom, m.minimum_stock_level, m.standard_unit_price
                ORDER BY m.item_name
                """
            ).fetchall()
        self.send_json(200, rows_to_dicts(rows))

    def create_material(self, user: dict) -> None:
        if user["role"] == "VIEWER":
            return self.send_json(403, {"error": {"code": "FORBIDDEN", "message": "Viewer cannot add materials"}})
        data = self.read_json()
        sku = str(data.get("sku", "")).strip().upper()
        item_name = str(data.get("itemName", "")).strip()
        category_id = data.get("categoryId")
        source_location = str(data.get("sourceLocation", "")).strip()
        destination_branch_id = data.get("destinationBranchId")
        uom = str(data.get("uom", "PCS")).strip().upper() or "PCS"
        description = str(data.get("description", "")).strip()
        try:
            minimum_stock_level = float(data.get("minimumStockLevel") or 0)
            standard_unit_price = float(data.get("standardUnitPrice") or 0)
            opening_quantity = float(data.get("openingQuantity") or 0)
            category_id = int(category_id)
            destination_branch_id = int(destination_branch_id) if destination_branch_id else None
        except (TypeError, ValueError):
            return self.send_json(400, {"error": {"code": "BAD_INPUT", "message": "Category, location, minimum stock, opening quantity, and unit price must be valid"}})
        if not sku or not item_name:
            return self.send_json(400, {"error": {"code": "BAD_INPUT", "message": "SKU and item name are required"}})
        if minimum_stock_level < 0 or standard_unit_price < 0 or opening_quantity < 0:
            return self.send_json(400, {"error": {"code": "BAD_INPUT", "message": "Minimum stock, opening quantity, and unit price cannot be negative"}})
        with db() as conn:
            exists = conn.execute("SELECT id FROM materials WHERE sku = ?", (sku,)).fetchone()
            if exists:
                return self.send_json(409, {"error": {"code": "DUPLICATE_SKU", "message": "A material with this SKU already exists"}})
            category = conn.execute("SELECT id FROM categories WHERE id = ?", (category_id,)).fetchone()
            if not category:
                return self.send_json(400, {"error": {"code": "BAD_CATEGORY", "message": "Selected category does not exist"}})
            if destination_branch_id:
                branch = conn.execute("SELECT id FROM branches WHERE id = ?", (destination_branch_id,)).fetchone()
                if not branch:
                    return self.send_json(400, {"error": {"code": "BAD_BRANCH", "message": "Selected destination branch does not exist"}})
            material_id = conn.execute(
                """
                INSERT INTO materials(sku, item_name, description, source_location, destination_branch_id, category_id, uom, minimum_stock_level, standard_unit_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (sku, item_name, description, source_location, destination_branch_id, category_id, uom, minimum_stock_level, standard_unit_price),
            ).fetchone()[0]
            if opening_quantity > 0 and destination_branch_id:
                conn.execute(
                    """
                    INSERT INTO inventory_balances(material_id, branch_id, condition, quantity_on_hand, average_unit_cost)
                    VALUES (?, ?, 'GOOD', ?, ?)
                    """,
                    (material_id, destination_branch_id, opening_quantity, standard_unit_price),
                )
                user_id = int(user["sub"])
                tx_no = f"MAT-{int(dt.datetime.now().timestamp())}-{secrets.randbelow(9999):04d}"
                tx = conn.execute(
                    """
                    INSERT INTO stock_transactions(transaction_no, transaction_type, branch_id, reference_no,
                      counterparty_name, department_or_client, transaction_date, remarks, created_by, created_at)
                    VALUES (?, 'INWARD', ?, ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (tx_no, destination_branch_id, f"NEW-{sku}", source_location, dt.date.today().isoformat(), "Opening stock from material creation", user_id, now_iso()),
                )
                conn.execute(
                    "INSERT INTO stock_transaction_lines(transaction_id, material_id, quantity, unit_price, condition_to) VALUES (?, ?, ?, ?, 'GOOD')",
                    (tx.lastrowid, material_id, opening_quantity, standard_unit_price),
                )
        self.send_json(201, {"id": material_id, "sku": sku})

    def create_branch(self, user: dict) -> None:
        if user["role"] != "ADMIN":
            return self.send_json(403, {"error": {"code": "FORBIDDEN", "message": "Admin access required to add branches"}})
        data = self.read_json()
        name = str(data.get("name", "")).strip()
        code = str(data.get("code", "")).strip().upper()
        branch_type = str(data.get("type", "BRANCH")).strip().upper() or "BRANCH"
        if not name:
            return self.send_json(400, {"error": {"code": "BAD_INPUT", "message": "Branch name is required"}})
        if not code:
            code = "".join(ch if ch.isalnum() else "-" for ch in name.upper()).strip("-")[:24]
        with db() as conn:
            existing = conn.execute("SELECT id FROM branches WHERE code = ?", (code,)).fetchone()
            if existing:
                return self.send_json(409, {"error": {"code": "DUPLICATE_BRANCH", "message": "A branch with this code already exists"}})
            branch_id = conn.execute(
                "INSERT INTO branches(code, name, type) VALUES (?, ?, ?) RETURNING id",
                (code, name, branch_type),
            ).fetchone()[0]
        self.send_json(201, {"id": branch_id, "code": code, "name": name, "type": branch_type})

    def transactions(self, query: dict) -> None:
        kind = query.get("type", ["ALL"])[0]
        clauses = []
        params = []
        if kind != "ALL":
            clauses.append("st.transaction_type = ?")
            params.append(kind)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with db() as conn:
            rows = conn.execute(
                f"""
                SELECT st.id, st.transaction_no, st.transaction_type, b.name AS branch,
                       st.reference_no, st.counterparty_name, st.department_or_client,
                       st.transaction_date, st.remarks, u.full_name AS created_by,
                       COUNT(stl.id) AS lines,
                       COALESCE(SUM(stl.quantity), 0) AS total_quantity,
                       COALESCE(SUM(stl.quantity * stl.unit_price), 0) AS total_value
                FROM stock_transactions st
                JOIN branches b ON b.id = st.branch_id
                JOIN users u ON u.id = st.created_by
                LEFT JOIN stock_transaction_lines stl ON stl.transaction_id = st.id
                {where}
                GROUP BY st.id, st.transaction_no, st.transaction_type, b.name, st.reference_no,
                         st.counterparty_name, st.department_or_client, st.transaction_date, st.remarks, u.full_name
                ORDER BY st.created_at DESC
                LIMIT 200
                """,
                params,
            ).fetchall()
        self.send_json(200, rows_to_dicts(rows))

    def report_stock(self, query: dict) -> None:
        branch_id = query.get("branchId", ["all"])[0]
        condition = query.get("condition", ["ALL"])[0]
        clauses = []
        params = []
        if branch_id != "all":
            clauses.append("b.id = ?")
            params.append(branch_id)
        if condition != "ALL":
            clauses.append("ib.condition = ?")
            params.append(condition)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with db() as conn:
            rows = conn.execute(
                f"""
                SELECT b.name AS branch, ib.condition, c.name AS category,
                       COUNT(DISTINCT m.id) AS item_count,
                       SUM(ib.quantity_on_hand) AS quantity,
                       ROUND(SUM(ib.quantity_on_hand * ib.average_unit_cost), 2) AS value
                FROM inventory_balances ib
                JOIN materials m ON m.id = ib.material_id
                JOIN categories c ON c.id = m.category_id
                JOIN branches b ON b.id = ib.branch_id
                {where}
                GROUP BY b.name, ib.condition, c.name
                ORDER BY b.name, ib.condition, c.name
                """,
                params,
            ).fetchall()
        self.send_json(200, rows_to_dicts(rows))

    def export_stock(self, query: dict) -> None:
        branch_id = query.get("branchId", ["all"])[0]
        condition = query.get("condition", ["ALL"])[0]
        clauses = []
        params = []
        if branch_id != "all":
            clauses.append("b.id = ?")
            params.append(branch_id)
        if condition != "ALL":
            clauses.append("ib.condition = ?")
            params.append(condition)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with db() as conn:
            rows = rows_to_dicts(conn.execute(
                f"""
                SELECT m.sku, m.item_name, c.name AS category, b.name AS branch, ib.condition,
                       ib.quantity_on_hand, m.uom, ib.average_unit_cost,
                       ROUND(ib.quantity_on_hand * ib.average_unit_cost, 2) AS stock_value
                FROM inventory_balances ib
                JOIN materials m ON m.id = ib.material_id
                JOIN categories c ON c.id = m.category_id
                JOIN branches b ON b.id = ib.branch_id
                {where}
                ORDER BY b.name, m.item_name
                """,
                params,
            ).fetchall())
        self.send_csv("prostarm-active-stock.csv", rows)

    def dashboard(self, query: dict) -> None:
        branch_id = query.get("branchId", ["all"])[0]
        branch_clause = "" if branch_id == "all" else "AND ib.branch_id = ?"
        params = [] if branch_id == "all" else [branch_id]
        with db() as conn:
            total_items = conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
            low_stock = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM inventory_balances ib
                JOIN materials m ON m.id = ib.material_id
                WHERE ib.condition = 'GOOD'
                  AND ib.quantity_on_hand <= m.minimum_stock_level
                  {branch_clause}
                """,
                params,
            ).fetchone()[0]
            valuation = conn.execute(
                f"""
                SELECT COALESCE(SUM(quantity_on_hand * average_unit_cost), 0)
                FROM inventory_balances ib
                WHERE 1=1 {branch_clause}
                """,
                params,
            ).fetchone()[0]
            recent = conn.execute("SELECT COUNT(*) FROM stock_transactions WHERE created_at >= ?", ((dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat(),)).fetchone()[0]
            by_condition = conn.execute(
                f"""
                SELECT condition, SUM(quantity_on_hand) AS quantity
                FROM inventory_balances ib
                WHERE 1=1 {branch_clause}
                GROUP BY condition
                ORDER BY condition
                """,
                params,
            ).fetchall()
        self.send_json(200, {
            "totalItems": total_items,
            "lowStockAlerts": low_stock,
            "totalValuation": round(float(valuation), 2),
            "recentActivity": recent,
            "byCondition": rows_to_dicts(by_condition),
        })

    def disposition(self, user: dict) -> None:
        if user["role"] == "VIEWER":
            return self.send_json(403, {"error": {"code": "FORBIDDEN", "message": "Viewer cannot create stock transactions"}})
        data = self.read_json()
        material_id = int(data["materialId"])
        branch_id = int(data["branchId"])
        qty = float(data["quantity"])
        to_condition = data.get("toCondition", "DAMAGED")
        if to_condition not in {"REJECTED", "DAMAGED", "BUYBACK", "SCRAP"}:
            return self.send_json(400, {"error": {"code": "BAD_CONDITION", "message": "Disposition condition must be rejected, damaged, buyback, or scrap"}})
        if qty <= 0:
            return self.send_json(400, {"error": {"code": "BAD_QUANTITY", "message": "Quantity must be greater than zero"}})
        with db() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT quantity_on_hand, average_unit_cost FROM inventory_balances WHERE material_id=? AND branch_id=? AND condition='GOOD'",
                    (material_id, branch_id),
                ).fetchone()
                available = float(row["quantity_on_hand"]) if row else 0
                cost = float(row["average_unit_cost"]) if row else 0
                if available < qty:
                    conn.rollback()
                    return self.send_json(409, {"error": {"code": "INSUFFICIENT_STOCK", "message": "Insufficient GOOD stock", "details": {"available": available, "requested": qty}}})
                user_id = int(user["sub"])
                tx_no = f"DSP-{int(dt.datetime.now().timestamp())}-{secrets.randbelow(9999):04d}"
                tx = conn.execute(
                    """
                    INSERT INTO stock_transactions(transaction_no, transaction_type, branch_id, reference_no,
                      counterparty_name, department_or_client, transaction_date, remarks, created_by, created_at)
                    VALUES (?, 'CONDITION_MOVE', ?, ?, NULL, NULL, ?, ?, ?, ?)
                    """,
                    (tx_no, branch_id, data.get("referenceNo"), data.get("date") or dt.date.today().isoformat(), data.get("remarks"), user_id, now_iso()),
                )
                tx_id = tx.lastrowid
                conn.execute(
                    "UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand - ? WHERE material_id=? AND branch_id=? AND condition='GOOD'",
                    (qty, material_id, branch_id),
                )
                conn.execute(
                    """
                    INSERT INTO inventory_balances(material_id, branch_id, condition, quantity_on_hand, average_unit_cost)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(material_id, branch_id, condition)
                    DO UPDATE SET quantity_on_hand = quantity_on_hand + excluded.quantity_on_hand,
                                  average_unit_cost = excluded.average_unit_cost
                    """,
                    (material_id, branch_id, to_condition, qty, cost),
                )
                conn.execute(
                    """
                    INSERT INTO stock_transaction_lines(transaction_id, material_id, quantity, unit_price, condition_from, condition_to)
                    VALUES (?, ?, ?, ?, 'GOOD', ?)
                    """,
                    (tx_id, material_id, qty, cost, to_condition),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.send_json(201, {"transactionNo": tx_no})

    def stock_move(self, kind: str, user: dict) -> None:
        if user["role"] == "VIEWER":
            return self.send_json(403, {"error": {"code": "FORBIDDEN", "message": "Viewer cannot create stock transactions"}})
        data = self.read_json()
        material_id = int(data["materialId"])
        branch_id = int(data["branchId"])
        qty = float(data["quantity"])
        if qty <= 0:
            return self.send_json(400, {"error": {"code": "BAD_QUANTITY", "message": "Quantity must be greater than zero"}})
        with db() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                user_id = int(user["sub"])
                tx_no = f"{kind[:3]}-{int(dt.datetime.now().timestamp())}-{secrets.randbelow(9999):04d}"
                tx = conn.execute(
                    """
                    INSERT INTO stock_transactions(transaction_no, transaction_type, branch_id, reference_no,
                      counterparty_name, department_or_client, transaction_date, remarks, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tx_no,
                        kind,
                        branch_id,
                        data.get("referenceNo"),
                        data.get("supplierName"),
                        data.get("departmentOrClient"),
                        data.get("date") or dt.date.today().isoformat(),
                        data.get("remarks"),
                        user_id,
                        now_iso(),
                    ),
                )
                tx_id = tx.lastrowid
                if kind == "INWARD":
                    unit_price = float(data.get("unitPrice") or 0)
                    condition = data.get("condition") or "GOOD"
                    conn.execute(
                        """
                        INSERT INTO inventory_balances(material_id, branch_id, condition, quantity_on_hand, average_unit_cost)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(material_id, branch_id, condition)
                        DO UPDATE SET quantity_on_hand = quantity_on_hand + excluded.quantity_on_hand,
                                      average_unit_cost = excluded.average_unit_cost
                        """,
                        (material_id, branch_id, condition, qty, unit_price),
                    )
                    conn.execute(
                        "INSERT INTO stock_transaction_lines(transaction_id, material_id, quantity, unit_price, condition_to) VALUES (?, ?, ?, ?, ?)",
                        (tx_id, material_id, qty, unit_price, condition),
                    )
                else:
                    row = conn.execute(
                        "SELECT quantity_on_hand FROM inventory_balances WHERE material_id=? AND branch_id=? AND condition='GOOD'",
                        (material_id, branch_id),
                    ).fetchone()
                    available = float(row["quantity_on_hand"]) if row else 0
                    if available < qty:
                        conn.rollback()
                        return self.send_json(409, {"error": {"code": "INSUFFICIENT_STOCK", "message": "Insufficient GOOD stock", "details": {"available": available, "requested": qty}}})
                    conn.execute(
                        "UPDATE inventory_balances SET quantity_on_hand = quantity_on_hand - ? WHERE material_id=? AND branch_id=? AND condition='GOOD'",
                        (qty, material_id, branch_id),
                    )
                    conn.execute(
                        "INSERT INTO stock_transaction_lines(transaction_id, material_id, quantity, unit_price, condition_from) VALUES (?, ?, ?, 0, 'GOOD')",
                        (tx_id, material_id, qty),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.send_json(201, {"transactionNo": tx_no})


def run() -> None:
    seed()
    server = ThreadingHTTPServer((HOST, PORT), App)
    print(f"ProstarM Inventory Management System running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run()
