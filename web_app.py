"""
web_app.py - Flask web interface for the payroll system.

Run:   python web_app.py      then open http://127.0.0.1:5000
First visit asks you to create the administrator account.

For real deployments: serve behind HTTPS with a production server
(e.g. waitress or gunicorn) and set PAYROLL_SECURE_COOKIES=1.
"""

import hmac
import os
import secrets
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

from flask import (Flask, Response, abort, flash, get_flashed_messages, redirect,
                   render_template, request, session, url_for)
from jinja2 import DictLoader

import payroll_core as core
from pdf_payslip import build_payslip_pdf

ZERO = Decimal("0")


def _secret_key():
    env = os.environ.get("PAYROLL_SECRET")
    if env:
        return env
    path = Path(__file__).with_name(".secret_key")
    if not path.exists():
        path.write_text(secrets.token_hex(32))
    return path.read_text().strip()


app = Flask(__name__)
app.secret_key = _secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("PAYROLL_SECURE_COOKIES") == "1",
)
core.init_db()

# ------------------------------- TEMPLATES --------------------------------
TEMPLATES = {
"base.html": """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Payroll{% endblock %}</title>
<style>
:root{--bg:#f6f7f9;--fg:#1f2933;--card:#fff;--line:#d9dee4;--accent:#1d4ed8;--bad:#b42318;--ok:#067647}
@media (prefers-color-scheme:dark){:root{--bg:#14181d;--fg:#e6e9ed;--card:#1d2329;--line:#333b44;--accent:#6ea0ff;--bad:#ff8a80;--ok:#6ee7a8}}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
nav{background:var(--card);border-bottom:1px solid var(--line);padding:10px 20px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}
nav a{color:var(--fg);text-decoration:none;font-weight:600}nav .sp{flex:1}
main{max-width:1000px;margin:24px auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:18px;overflow-x:auto}
table{border-collapse:collapse;width:100%}th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
td.n,th.n{text-align:right}
input,select{padding:7px 9px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit;max-width:100%}
td input{width:90px}label{display:block;margin:10px 0 3px;font-weight:600}
.btn,button{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:8px 14px;font:inherit;cursor:pointer;text-decoration:none;display:inline-block}
button.link{background:none;color:var(--accent);padding:0}button.bad,.bad{color:var(--bad)}button.bad{background:none;padding:0}
.flash{padding:9px 14px;border-radius:6px;margin-bottom:12px;border:1px solid var(--line)}.flash.bad{color:var(--bad)}.flash.ok{color:var(--ok)}
.row{display:flex;gap:10px;align-items:end;flex-wrap:wrap}h1{margin-top:0}.muted{opacity:.7}
</style></head><body>
{% if session.get('user') %}<nav><a href="{{ url_for('employees') }}">Employees</a>
<a href="{{ url_for('attendance') }}">Attendance</a><a href="{{ url_for('payroll') }}">Run payroll</a>
<a href="{{ url_for('summary') }}">Summary</a><span class="sp"></span>
<span class="muted">{{ session['user'] }}</span>
<form method="post" action="{{ url_for('logout') }}" style="margin:0"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><button class="link">Log out</button></form></nav>{% endif %}
<main>{% for cat, msg in get_flashed_messages(with_categories=true) %}<div class="flash {{ cat }}">{{ msg }}</div>{% endfor %}
{% block body %}{% endblock %}</main></body></html>""",

"auth.html": """{% extends "base.html" %}{% block body %}<div class="card" style="max-width:380px;margin:40px auto">
<h1>{{ heading }}</h1><form method="post"><input type="hidden" name="_csrf" value="{{ csrf_token() }}">
<label>Username</label><input name="username" required autofocus style="width:100%">
<label>Password</label><input name="password" type="password" required style="width:100%">
{% if setup %}<label>Confirm password</label><input name="confirm" type="password" required style="width:100%">
<p class="muted">At least 8 characters.</p>{% endif %}
<p><button>{{ heading }}</button></p></form></div>{% endblock %}""",

"employees.html": """{% extends "base.html" %}{% block body %}<div class="card"><h1>Employees</h1>
<p><a class="btn" href="{{ url_for('employee_new') }}">Add employee</a></p>
<table><tr><th>ID</th><th>Name</th><th>Department</th><th>Designation</th><th class="n">Basic</th><th class="n">HRA %</th><th class="n">Allowances</th><th></th></tr>
{% for e in employees %}<tr><td>{{ e.id }}</td><td>{{ e.name }}</td><td>{{ e.department }}</td><td>{{ e.designation }}</td>
<td class="n">{{ e.basic|money }}</td><td class="n">{{ e.hra_pct }}</td><td class="n">{{ e.allowances|money }}</td>
<td><a href="{{ url_for('employee_edit', emp_id=e.id) }}">Edit</a> &nbsp;
<form method="post" action="{{ url_for('employee_delete', emp_id=e.id) }}" style="display:inline" onsubmit="return confirm('Delete this employee and all their payslips?')">
<input type="hidden" name="_csrf" value="{{ csrf_token() }}"><button class="bad">Delete</button></form></td></tr>
{% else %}<tr><td colspan="8" class="muted">No employees yet.</td></tr>{% endfor %}</table></div>{% endblock %}""",

"employee_form.html": """{% extends "base.html" %}{% block body %}<div class="card" style="max-width:520px">
<h1>{{ 'Edit' if editing else 'Add' }} employee</h1><form method="post"><input type="hidden" name="_csrf" value="{{ csrf_token() }}">
<label>Full name</label><input name="name" value="{{ e.name or '' }}" required style="width:100%">
<label>Department</label><input name="department" value="{{ e.department or '' }}" style="width:100%">
<label>Designation</label><input name="designation" value="{{ e.designation or '' }}" style="width:100%">
<label>Monthly basic salary</label><input name="basic" value="{{ e.basic or '' }}" required inputmode="decimal">
<label>HRA (% of basic)</label><input name="hra_pct" value="{{ e.hra_pct or '40' }}" required inputmode="decimal">
<label>Other monthly allowances</label><input name="allowances" value="{{ e.allowances or '0' }}" required inputmode="decimal">
{% if not editing %}<label>Join date</label><input name="join_date" type="date" value="{{ today }}">{% endif %}
<p><button>Save</button> &nbsp; <a href="{{ url_for('employees') }}">Cancel</a></p></form></div>{% endblock %}""",

"attendance.html": """{% extends "base.html" %}{% block body %}<div class="card"><h1>Attendance</h1>
<form method="get" class="row"><div><label>Month</label><input type="month" name="month" value="{{ month }}"></div><button>Load</button></form></div>
<div class="card"><form method="post"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><input type="hidden" name="month" value="{{ month }}">
<table><tr><th>ID</th><th>Name</th><th>Working days</th><th>Loss-of-pay days</th></tr>
{% for e in employees %}<tr><td>{{ e.id }}</td><td>{{ e.name }}</td>
<td><input name="wd_{{ e.id }}" value="{{ att.get(e.id, (default_days, 0))[0] }}" inputmode="numeric"></td>
<td><input name="lop_{{ e.id }}" value="{{ att.get(e.id, (default_days, 0))[1] }}" inputmode="decimal"></td></tr>
{% else %}<tr><td colspan="4" class="muted">Add employees first.</td></tr>{% endfor %}</table>
{% if employees %}<p><button>Save attendance</button></p>{% endif %}</form>
<p class="muted">Salary for LOP days = (basic + HRA + allowances) &divide; working days &times; LOP days. Half days: use 0.5.</p></div>{% endblock %}""",

"payroll.html": """{% extends "base.html" %}{% block body %}<div class="card"><h1>Run payroll</h1>
<form method="get" class="row"><div><label>Month</label><input type="month" name="month" value="{{ month }}"></div><button>Load</button></form></div>
<div class="card"><form method="post"><input type="hidden" name="_csrf" value="{{ csrf_token() }}"><input type="hidden" name="month" value="{{ month }}">
<table><tr><th>ID</th><th>Name</th><th>Working / LOP days</th><th>Bonus / overtime</th><th>Other deductions</th><th>Status</th></tr>
{% for e in employees %}{% set done = e.id in done_ids %}<tr><td>{{ e.id }}</td><td>{{ e.name }}</td>
<td>{% set a = att.get(e.id, (default_days, 0)) %}{{ a[0] }} / {{ a[1] }}</td>
{% if done %}<td colspan="2" class="muted">already processed</td><td><a href="{{ url_for('payslip', emp_id=e.id, month=month) }}">View payslip</a></td>
{% else %}<td><input name="bonus_{{ e.id }}" value="0" inputmode="decimal"></td><td><input name="other_{{ e.id }}" value="0" inputmode="decimal"></td><td class="muted">pending</td>{% endif %}</tr>
{% else %}<tr><td colspan="6" class="muted">Add employees first.</td></tr>{% endfor %}</table>
{% if employees %}<p><button>Run payroll for {{ month }}</button></p>{% endif %}</form></div>{% endblock %}""",

"summary.html": """{% extends "base.html" %}{% block body %}<div class="card"><h1>Monthly summary</h1>
<form method="get" class="row"><div><label>Month</label><input type="month" name="month" value="{{ month }}"></div><button>Load</button>
{% if slips %}<a class="btn" href="{{ url_for('export', month=month) }}">Export CSV</a>{% endif %}</form></div>
<div class="card"><table><tr><th>ID</th><th>Name</th><th class="n">Gross</th><th class="n">Deductions</th><th class="n">Net pay</th><th></th></tr>
{% for s in slips %}<tr><td>{{ s.emp_id }}</td><td>{{ s.name }}</td><td class="n">{{ s.gross|money }}</td><td class="n">{{ s.total_ded|money }}</td><td class="n">{{ s.net_pay|money }}</td>
<td><a href="{{ url_for('payslip', emp_id=s.emp_id, month=month) }}">View</a> &nbsp; <a href="{{ url_for('payslip_pdf', emp_id=s.emp_id, month=month) }}">PDF</a></td></tr>
{% else %}<tr><td colspan="6" class="muted">No payroll run for this month.</td></tr>{% endfor %}
{% if slips %}<tr><th colspan="2">Total</th><th class="n">{{ totals.gross|money }}</th><th class="n">{{ totals.ded|money }}</th><th class="n">{{ totals.net|money }}</th><th></th></tr>{% endif %}</table></div>{% endblock %}""",

"payslip.html": """{% extends "base.html" %}{% block body %}<div class="card" style="max-width:560px">
<h1>Payslip - {{ month }}</h1><p><b>{{ s.name }}</b> (ID {{ s.emp_id }})<br><span class="muted">{{ s.department }} {{ '·' if s.department and s.designation }} {{ s.designation }}</span><br>
<span class="muted">Working days {{ s.working_days }}, loss-of-pay days {{ s.lop_days|days }}</span></p>
<table>
<tr><th colspan="2">Earnings</th></tr>
<tr><td>Basic</td><td class="n">{{ s.basic|money }}</td></tr><tr><td>HRA</td><td class="n">{{ s.hra|money }}</td></tr>
<tr><td>Allowances</td><td class="n">{{ s.allowances|money }}</td></tr><tr><td>Bonus / overtime</td><td class="n">{{ s.bonus|money }}</td></tr>
<tr><th>Gross</th><th class="n">{{ s.gross|money }}</th></tr>
<tr><th colspan="2">Deductions</th></tr>
<tr><td>Provident fund</td><td class="n">{{ s.pf|money }}</td></tr><tr><td>Income tax (est.)</td><td class="n">{{ s.tax|money }}</td></tr>
<tr><td>Loss-of-pay</td><td class="n">{{ s.lop_deduction|money }}</td></tr><tr><td>Other deductions</td><td class="n">{{ s.other_ded|money }}</td></tr>
<tr><th>Total deductions</th><th class="n">{{ s.total_ded|money }}</th></tr>
<tr><th>NET PAY</th><th class="n">{{ s.net_pay|money }}</th></tr></table>
<p class="row"><a class="btn" href="{{ url_for('payslip_pdf', emp_id=s.emp_id, month=month) }}">Download PDF</a>
<form method="post" action="{{ url_for('payslip_delete', emp_id=s.emp_id, month=month) }}" onsubmit="return confirm('Delete this payslip so payroll can be re-run for this employee?')">
<input type="hidden" name="_csrf" value="{{ csrf_token() }}"><button class="bad">Delete payslip</button></form></p></div>{% endblock %}""",
}
app.jinja_loader = DictLoader(TEMPLATES)
app.jinja_env.filters["money"] = lambda v: f"{v:,.2f}"
app.jinja_env.filters["days"] = lambda v: format(v.normalize(), "f")


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token

# ------------------------------- SECURITY ---------------------------------
_fails = {}


def _locked_out(ip):
    now = time.time()
    _fails[ip] = [t for t in _fails.get(ip, []) if now - t < 300]
    return len(_fails[ip]) >= 5


@app.before_request
def guard():
    if request.endpoint in (None, "static"):
        return None
    if request.method == "POST":
        sent = request.form.get("_csrf", "").encode()
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "").encode()):
            abort(400)
    if not core.has_users():
        if request.endpoint != "setup":
            return redirect(url_for("setup"))
    elif request.endpoint not in ("login", "setup") and not session.get("user"):
        return redirect(url_for("login"))
    return None


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# --------------------------------- ROUTES ---------------------------------
def month_arg():
    value = request.values.get("month") or date.today().strftime("%Y-%m")
    try:
        return core.validate_month(value)
    except ValueError:
        flash("Invalid month - showing the current month.", "bad")
        return date.today().strftime("%Y-%m")


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if core.has_users():
        return redirect(url_for("login"))
    if request.method == "POST":
        if request.form.get("password") != request.form.get("confirm"):
            flash("Passwords do not match.", "bad")
        else:
            try:
                core.create_user(request.form.get("username"), request.form.get("password"))
                flash("Administrator created. Please log in.", "ok")
                return redirect(url_for("login"))
            except ValueError as err:
                flash(str(err), "bad")
    return render_template("auth.html", heading="Create administrator", setup=True)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _locked_out(ip):
            flash("Too many failed attempts. Try again in a few minutes.", "bad")
        elif core.verify_user(request.form.get("username"), request.form.get("password")):
            session.clear()
            session["user"] = request.form["username"].strip()
            return redirect(url_for("employees"))
        else:
            _fails.setdefault(ip, []).append(time.time())
            flash("Invalid username or password.", "bad")
    return render_template("auth.html", heading="Log in", setup=False)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return redirect(url_for("employees"))


@app.route("/employees")
def employees():
    return render_template("employees.html", employees=core.list_employees())


def _employee_fields():
    f = request.form
    return (f.get("name"), f.get("department"), f.get("designation"),
            f.get("basic"), f.get("hra_pct"), f.get("allowances"))


@app.route("/employees/new", methods=["GET", "POST"])
def employee_new():
    if request.method == "POST":
        try:
            core.add_employee(*_employee_fields(), join_date=request.form.get("join_date"))
            flash("Employee added.", "ok")
            return redirect(url_for("employees"))
        except ValueError as err:
            flash(str(err), "bad")
    return render_template("employee_form.html", e=request.form, editing=False,
                           today=date.today().isoformat())


@app.route("/employees/<int:emp_id>/edit", methods=["GET", "POST"])
def employee_edit(emp_id):
    emp = core.get_employee(emp_id)
    if not emp:
        abort(404)
    if request.method == "POST":
        try:
            core.update_employee(emp_id, *_employee_fields())
            flash("Employee updated. Existing payslips are unchanged.", "ok")
            return redirect(url_for("employees"))
        except ValueError as err:
            flash(str(err), "bad")
            emp = request.form
    return render_template("employee_form.html", e=emp, editing=True)


@app.post("/employees/<int:emp_id>/delete")
def employee_delete(emp_id):
    core.delete_employee(emp_id)
    flash("Employee deleted.", "ok")
    return redirect(url_for("employees"))


@app.route("/attendance", methods=["GET", "POST"])
def attendance():
    month = month_arg()
    employees_ = core.list_employees()
    if request.method == "POST":
        saved = 0
        for e in employees_:
            try:
                core.set_attendance(e["id"], month, request.form.get(f"wd_{e['id']}"),
                                    request.form.get(f"lop_{e['id']}"))
                saved += 1
            except ValueError as err:
                flash(f"{e['name']}: {err}", "bad")
        if saved:
            flash(f"Attendance saved for {saved} employee(s).", "ok")
        return redirect(url_for("attendance", month=month))
    return render_template("attendance.html", month=month, employees=employees_,
                           att=core.attendance_for_month(month),
                           default_days=core.DEFAULT_WORKING_DAYS)


@app.route("/payroll", methods=["GET", "POST"])
def payroll():
    month = month_arg()
    employees_ = core.list_employees()
    if request.method == "POST":
        adjustments = {}
        for e in employees_:
            try:
                adjustments[e["id"]] = (
                    core.D(request.form.get(f"bonus_{e['id']}") or "0", "Bonus"),
                    core.D(request.form.get(f"other_{e['id']}") or "0", "Other deductions"),
                )
            except ValueError as err:
                flash(f"{e['name']}: {err}", "bad")
                return redirect(url_for("payroll", month=month))
        processed, skipped = core.run_payroll(month, adjustments)
        flash(f"Payroll for {month}: {len(processed)} processed.", "ok")
        for name, reason in skipped:
            if reason != "payslip already exists":
                flash(f"{name} skipped: {reason}", "bad")
        return redirect(url_for("summary", month=month))
    done_ids = {s["emp_id"] for s in core.list_payslips(month)}
    return render_template("payroll.html", month=month, employees=employees_,
                           done_ids=done_ids, att=core.attendance_for_month(month),
                           default_days=core.DEFAULT_WORKING_DAYS)


@app.route("/summary")
def summary():
    month = month_arg()
    slips = core.list_payslips(month)
    totals = {
        "gross": sum((s["gross"] for s in slips), ZERO),
        "ded": sum((s["total_ded"] for s in slips), ZERO),
        "net": sum((s["net_pay"] for s in slips), ZERO),
    }
    return render_template("summary.html", month=month, slips=slips, totals=totals)


@app.route("/payslip/<int:emp_id>/<month>")
def payslip(emp_id, month):
    try:
        core.validate_month(month)
    except ValueError:
        abort(404)
    s = core.get_payslip(emp_id, month)
    if not s:
        abort(404)
    return render_template("payslip.html", s=s, month=month)


@app.route("/payslip/<int:emp_id>/<month>.pdf")
def payslip_pdf(emp_id, month):
    try:
        core.validate_month(month)
    except ValueError:
        abort(404)
    s = core.get_payslip(emp_id, month)
    if not s:
        abort(404)
    pdf = build_payslip_pdf(s, core.COMPANY_NAME)
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f'inline; filename="payslip_{emp_id}_{month}.pdf"'})


@app.post("/payslip/<int:emp_id>/<month>/delete")
def payslip_delete(emp_id, month):
    core.validate_month(month)
    core.delete_payslip(emp_id, month)
    flash("Payslip deleted. You can re-run payroll for this month.", "ok")
    return redirect(url_for("summary", month=month))


@app.route("/export/<month>.csv")
def export(month):
    try:
        core.validate_month(month)
    except ValueError:
        abort(404)
    return Response(core.export_csv(month), mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="payroll_{month}.csv"'})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
