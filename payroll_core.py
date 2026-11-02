"""
payroll_core.py - shared business logic for the web and desktop front ends.

  * Money uses Decimal (stored as text in SQLite) so there are no float errors.
  * Admin accounts use salted PBKDF2 password hashes.
  * Attendance records drive loss-of-pay (LOP) deductions.

The tax slabs and rates below are PLACEHOLDERS - change them to match the
rules that apply to your company before using this for real payroll.
"""

import csv
import hashlib
import hmac
import io
import os
import secrets
import sqlite3
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

DB_FILE = Path(os.environ.get("PAYROLL_DB", Path(__file__).with_name("payroll_app.db")))
COMPANY_NAME = os.environ.get("PAYROLL_COMPANY", "Your Company Pvt Ltd")

# ----------------------------- CONFIGURATION ------------------------------
PF_RATE = Decimal("0.12")               # % of basic
STANDARD_DEDUCTION = Decimal("50000")   # annual, before applying slabs
TAX_SLABS = [                           # (upper limit, rate); None = no limit
    (Decimal("300000"), Decimal("0")),
    (Decimal("700000"), Decimal("0.05")),
    (Decimal("1000000"), Decimal("0.10")),
    (Decimal("1200000"), Decimal("0.15")),
    (Decimal("1500000"), Decimal("0.20")),
    (None, Decimal("0.30")),
]
DEFAULT_WORKING_DAYS = 30
# --------------------------------------------------------------------------

CENT = Decimal("0.01")
ZERO = Decimal("0")
EMP_MONEY = ("basic", "hra_pct", "allowances")
SLIP_MONEY = ("basic", "hra", "allowances", "bonus", "gross", "pf", "tax",
              "lop_deduction", "other_ded", "total_ded", "net_pay")


# ------------------------------- HELPERS ----------------------------------
def D(value, field="Value", minimum=ZERO):
    """Parse user input into a Decimal or raise a readable ValueError."""
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a number")
    if not d.is_finite():
        raise ValueError(f"{field} must be a number")
    if d < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    return d


def r2(x):
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def validate_month(month):
    try:
        datetime.strptime(month, "%Y-%m")
    except (TypeError, ValueError):
        raise ValueError("Month must look like 2026-11")
    return month


def connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _convert(row, money_cols):
    d = dict(row)
    for c in money_cols:
        d[c] = Decimal(d[c])
    if "lop_days" in d:
        d["lop_days"] = Decimal(d["lop_days"])
    return d


def init_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                salt TEXT NOT NULL,
                pw_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                department TEXT,
                designation TEXT,
                basic TEXT NOT NULL,
                hra_pct TEXT NOT NULL,
                allowances TEXT NOT NULL,
                join_date TEXT
            );
            CREATE TABLE IF NOT EXISTS attendance (
                emp_id INTEGER NOT NULL,
                month TEXT NOT NULL,
                working_days INTEGER NOT NULL,
                lop_days TEXT NOT NULL,
                PRIMARY KEY (emp_id, month),
                FOREIGN KEY (emp_id) REFERENCES employees(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS payslips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id INTEGER NOT NULL,
                month TEXT NOT NULL,
                working_days INTEGER NOT NULL,
                lop_days TEXT NOT NULL,
                basic TEXT, hra TEXT, allowances TEXT, bonus TEXT, gross TEXT,
                pf TEXT, tax TEXT, lop_deduction TEXT, other_ded TEXT,
                total_ded TEXT, net_pay TEXT,
                UNIQUE (emp_id, month),
                FOREIGN KEY (emp_id) REFERENCES employees(id) ON DELETE CASCADE
            );
            """
        )


# --------------------------------- AUTH -----------------------------------
def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)


def has_users():
    with connect() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def create_user(username, password):
    username = (username or "").strip()
    if not username:
        raise ValueError("Username is required")
    if len(password or "") < 8:
        raise ValueError("Password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO users (username, salt, pw_hash) VALUES (?,?,?)",
                (username, salt.hex(), _hash(password, salt).hex()),
            )
    except sqlite3.IntegrityError:
        raise ValueError("That username already exists")


def verify_user(username, password):
    with connect() as conn:
        row = conn.execute(
            "SELECT salt, pw_hash FROM users WHERE username = ?", ((username or "").strip(),)
        ).fetchone()
    if row is None:
        _hash(password or "", b"\x00" * 16)  # keep timing similar
        return False
    candidate = _hash(password or "", bytes.fromhex(row["salt"])).hex()
    return hmac.compare_digest(candidate, row["pw_hash"])


# ------------------------------- EMPLOYEES --------------------------------
def _clean_employee(name, department, designation, basic, hra_pct, allowances):
    name = (name or "").strip()
    if not name:
        raise ValueError("Name is required")
    hra = D(hra_pct, "HRA %")
    if hra > 100:
        raise ValueError("HRA % cannot exceed 100")
    return (
        name,
        (department or "").strip(),
        (designation or "").strip(),
        str(D(basic, "Basic salary", Decimal("0.01"))),
        str(hra),
        str(D(allowances, "Allowances")),
    )


def add_employee(name, department, designation, basic, hra_pct, allowances, join_date=None):
    vals = _clean_employee(name, department, designation, basic, hra_pct, allowances)
    join_date = (join_date or date.today().isoformat()).strip()
    try:
        date.fromisoformat(join_date)
    except ValueError:
        raise ValueError("Join date must look like 2026-01-31")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO employees (name, department, designation, basic, hra_pct, "
            "allowances, join_date) VALUES (?,?,?,?,?,?,?)",
            vals + (join_date,),
        )
    return cur.lastrowid


def update_employee(emp_id, name, department, designation, basic, hra_pct, allowances):
    vals = _clean_employee(name, department, designation, basic, hra_pct, allowances)
    with connect() as conn:
        cur = conn.execute(
            "UPDATE employees SET name=?, department=?, designation=?, basic=?, "
            "hra_pct=?, allowances=? WHERE id=?",
            vals + (emp_id,),
        )
    if cur.rowcount == 0:
        raise ValueError("Employee not found")


def delete_employee(emp_id):
    with connect() as conn:
        conn.execute("DELETE FROM employees WHERE id = ?", (emp_id,))


def get_employee(emp_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM employees WHERE id = ?", (emp_id,)).fetchone()
    return _convert(row, EMP_MONEY) if row else None


def list_employees():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM employees ORDER BY id").fetchall()
    return [_convert(r, EMP_MONEY) for r in rows]


# ------------------------------- ATTENDANCE -------------------------------
def set_attendance(emp_id, month, working_days, lop_days):
    validate_month(month)
    try:
        wd = int(str(working_days).strip())
    except ValueError:
        raise ValueError("Working days must be a whole number")
    if not 1 <= wd <= 31:
        raise ValueError("Working days must be between 1 and 31")
    lop = D(lop_days, "LOP days")
    if lop > wd:
        raise ValueError("LOP days cannot exceed working days")
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO attendance (emp_id, month, working_days, lop_days) "
                "VALUES (?,?,?,?) ON CONFLICT(emp_id, month) DO UPDATE SET "
                "working_days=excluded.working_days, lop_days=excluded.lop_days",
                (emp_id, month, wd, str(lop)),
            )
    except sqlite3.IntegrityError:
        raise ValueError("Employee not found")


def attendance_for_month(month):
    """{emp_id: (working_days, lop_days)} for records that exist."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT emp_id, working_days, lop_days FROM attendance WHERE month = ?", (month,)
        ).fetchall()
    return {r["emp_id"]: (r["working_days"], Decimal(r["lop_days"])) for r in rows}


# ------------------------------ CALCULATIONS ------------------------------
def annual_tax(taxable):
    tax, lower = ZERO, ZERO
    for upper, rate in TAX_SLABS:
        if taxable <= lower:
            break
        top = taxable if upper is None else min(taxable, upper)
        tax += (top - lower) * rate
        if upper is None:
            break
        lower = upper
    return tax


def calculate_pay(emp, working_days=DEFAULT_WORKING_DAYS, lop_days=ZERO,
                  bonus=ZERO, other_ded=ZERO):
    basic = emp["basic"]
    hra = r2(basic * emp["hra_pct"] / 100)
    fixed = basic + hra + emp["allowances"]
    gross = fixed + bonus

    pf = r2(basic * PF_RATE)
    taxable = max(ZERO, fixed * 12 - STANDARD_DEDUCTION)
    tax = r2(annual_tax(taxable) / 12)
    lop_ded = r2(fixed / working_days * lop_days)

    total = pf + tax + lop_ded + other_ded
    net = gross - total
    if net < 0:
        raise ValueError("deductions exceed gross pay")
    return {
        "basic": basic, "hra": hra, "allowances": emp["allowances"], "bonus": bonus,
        "gross": gross, "pf": pf, "tax": tax, "lop_deduction": lop_ded,
        "other_ded": other_ded, "total_ded": total, "net_pay": net,
    }


# --------------------------------- PAYROLL --------------------------------
def run_payroll(month, adjustments=None):
    """
    adjustments: {emp_id: (bonus, other_deductions)} as Decimals.
    Returns (processed_names, [(name, reason), ...] skipped).
    """
    validate_month(month)
    adjustments = adjustments or {}
    attendance = attendance_for_month(month)
    processed, skipped = [], []
    with connect() as conn:
        employees = [_convert(r, EMP_MONEY) for r in
                     conn.execute("SELECT * FROM employees ORDER BY id")]
        existing = {r["emp_id"] for r in
                    conn.execute("SELECT emp_id FROM payslips WHERE month = ?", (month,))}
        for emp in employees:
            if emp["id"] in existing:
                skipped.append((emp["name"], "payslip already exists"))
                continue
            wd, lop = attendance.get(emp["id"], (DEFAULT_WORKING_DAYS, ZERO))
            bonus, other = adjustments.get(emp["id"], (ZERO, ZERO))
            try:
                p = calculate_pay(emp, wd, lop, bonus, other)
            except ValueError as err:
                skipped.append((emp["name"], str(err)))
                continue
            conn.execute(
                "INSERT INTO payslips (emp_id, month, working_days, lop_days, basic, hra, "
                "allowances, bonus, gross, pf, tax, lop_deduction, other_ded, total_ded, "
                "net_pay) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (emp["id"], month, wd, str(lop)) + tuple(str(p[c]) for c in SLIP_MONEY),
            )
            processed.append(emp["name"])
    return processed, skipped


_SLIP_SQL = (
    "SELECT p.*, e.name, e.department, e.designation FROM payslips p "
    "JOIN employees e ON e.id = p.emp_id "
)


def get_payslip(emp_id, month):
    with connect() as conn:
        row = conn.execute(_SLIP_SQL + "WHERE p.emp_id = ? AND p.month = ?",
                           (emp_id, month)).fetchone()
    return _convert(row, SLIP_MONEY) if row else None


def list_payslips(month):
    with connect() as conn:
        rows = conn.execute(_SLIP_SQL + "WHERE p.month = ? ORDER BY p.emp_id", (month,)).fetchall()
    return [_convert(r, SLIP_MONEY) for r in rows]


def delete_payslip(emp_id, month):
    with connect() as conn:
        conn.execute("DELETE FROM payslips WHERE emp_id = ? AND month = ?", (emp_id, month))


def export_csv(month):
    cols = ["emp_id", "name", "department", "month", "working_days", "lop_days"] + list(SLIP_MONEY)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(cols)
    for s in list_payslips(month):
        writer.writerow([s[c] for c in cols])
    return out.getvalue()
