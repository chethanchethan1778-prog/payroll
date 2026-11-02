"""
desktop_app.py - Tkinter desktop interface for the payroll system.

Run:   python desktop_app.py
First launch asks you to create the administrator account.
(Tkinter ships with Python on Windows/macOS; on Linux install python3-tk.)
"""

import tkinter as tk
from datetime import date
from decimal import Decimal
from tkinter import filedialog, messagebox, simpledialog, ttk

import payroll_core as core
from pdf_payslip import build_payslip_pdf

ZERO = Decimal("0")


def guarded(fn):
    """Show validation errors in a dialog instead of crashing."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as err:
            messagebox.showerror("Error", str(err))
    return wrapper


def money(x):
    return f"{x:,.2f}"


def make_tree(parent, columns):
    """columns: list of (key, heading, width, anchor)."""
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", height=14)
    for key, heading, width, anchor in columns:
        tree.heading(key, text=heading)
        tree.column(key, width=width, anchor=anchor)
    scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    frame.pack(fill="both", expand=True, padx=8, pady=8)
    return tree


def selected_id(tree):
    sel = tree.selection()
    if not sel:
        messagebox.showinfo("Select a row", "Please select a row first.")
        return None
    return int(tree.item(sel[0], "values")[0])


# --------------------------------- LOGIN ----------------------------------
def login_flow(root):
    if not core.has_users():
        messagebox.showinfo("First run", "Create the administrator account.")
        while True:
            user = simpledialog.askstring("Setup", "Username:", parent=root)
            if user is None:
                return False
            pw = simpledialog.askstring("Setup", "Password (min 8 characters):",
                                        show="*", parent=root)
            if pw is None:
                return False
            try:
                core.create_user(user, pw)
                return True
            except ValueError as err:
                messagebox.showerror("Error", str(err))
    for _ in range(3):
        user = simpledialog.askstring("Log in", "Username:", parent=root)
        if user is None:
            return False
        pw = simpledialog.askstring("Log in", "Password:", show="*", parent=root)
        if pw is None:
            return False
        if core.verify_user(user, pw):
            return True
        messagebox.showerror("Log in", "Invalid username or password.")
    return False


# ----------------------------- EMPLOYEE DIALOG ----------------------------
class EmployeeDialog(tk.Toplevel):
    FIELDS = [("name", "Full name"), ("department", "Department"),
              ("designation", "Designation"), ("basic", "Monthly basic salary"),
              ("hra_pct", "HRA (% of basic)"), ("allowances", "Other allowances"),
              ("join_date", "Join date (YYYY-MM-DD)")]

    def __init__(self, parent, emp=None, on_saved=None):
        super().__init__(parent)
        self.title("Edit employee" if emp else "Add employee")
        self.emp, self.on_saved = emp, on_saved
        defaults = emp or {"hra_pct": "40", "allowances": "0",
                           "join_date": date.today().isoformat()}
        self.vars = {}
        row = 0
        for key, label in self.FIELDS:
            if emp and key == "join_date":
                continue
            ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=4)
            var = tk.StringVar(value=str(defaults.get(key, "") or ""))
            ttk.Entry(self, textvariable=var, width=30).grid(row=row, column=1, padx=10, pady=4)
            self.vars[key] = var
            row += 1
        ttk.Button(self, text="Save", command=self.save).grid(row=row, column=0, columnspan=2, pady=10)
        self.transient(parent)
        self.grab_set()

    @guarded
    def save(self):
        v = {k: var.get() for k, var in self.vars.items()}
        args = (v["name"], v["department"], v["designation"], v["basic"],
                v["hra_pct"], v["allowances"])
        if self.emp:
            core.update_employee(self.emp["id"], *args)
        else:
            core.add_employee(*args, join_date=v["join_date"])
        if self.on_saved:
            self.on_saved()
        self.destroy()


# --------------------------------- MAIN UI --------------------------------
class MainUI:
    def __init__(self, root):
        self.root = root
        root.title("Employee Payroll")
        root.geometry("900x560")
        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True)
        self.month = tk.StringVar(value=date.today().strftime("%Y-%m"))
        self._build_employees(nb)
        self._build_attendance(nb)
        self._build_payroll(nb)
        self.refresh_all()

    # ---- employees tab
    def _build_employees(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Employees")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(bar, text="Add", command=self.add_emp).pack(side="left")
        ttk.Button(bar, text="Edit", command=self.edit_emp).pack(side="left", padx=6)
        ttk.Button(bar, text="Delete", command=self.delete_emp).pack(side="left")
        self.emp_tree = make_tree(tab, [
            ("id", "ID", 50, "center"), ("name", "Name", 220, "w"),
            ("dept", "Department", 140, "w"), ("desig", "Designation", 160, "w"),
            ("basic", "Basic", 110, "e"), ("allow", "Allowances", 110, "e")])

    def add_emp(self):
        EmployeeDialog(self.root, on_saved=self.refresh_all)

    def edit_emp(self):
        emp_id = selected_id(self.emp_tree)
        if emp_id:
            EmployeeDialog(self.root, emp=core.get_employee(emp_id), on_saved=self.refresh_all)

    def delete_emp(self):
        emp_id = selected_id(self.emp_tree)
        if emp_id and messagebox.askyesno("Delete", "Delete this employee and all their payslips?"):
            core.delete_employee(emp_id)
            self.refresh_all()

    # ---- attendance tab
    def _build_attendance(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Attendance")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Label(bar, text="Month (YYYY-MM):").pack(side="left")
        ttk.Entry(bar, textvariable=self.month, width=10).pack(side="left", padx=6)
        ttk.Button(bar, text="Load", command=self.refresh_all).pack(side="left")
        ttk.Button(bar, text="Edit selected", command=self.edit_attendance).pack(side="left", padx=6)
        self.att_tree = make_tree(tab, [
            ("id", "ID", 50, "center"), ("name", "Name", 250, "w"),
            ("wd", "Working days", 120, "e"), ("lop", "Loss-of-pay days", 140, "e")])

    @guarded
    def edit_attendance(self):
        emp_id = selected_id(self.att_tree)
        if not emp_id:
            return
        month = core.validate_month(self.month.get().strip())
        wd, lop = core.attendance_for_month(month).get(emp_id, (core.DEFAULT_WORKING_DAYS, ZERO))
        new_wd = simpledialog.askstring("Attendance", "Working days:", initialvalue=str(wd), parent=self.root)
        if new_wd is None:
            return
        new_lop = simpledialog.askstring("Attendance", "Loss-of-pay days:", initialvalue=str(lop), parent=self.root)
        if new_lop is None:
            return
        core.set_attendance(emp_id, month, new_wd, new_lop)
        self.refresh_all()

    # ---- payroll tab
    def _build_payroll(self, nb):
        tab = ttk.Frame(nb)
        nb.add(tab, text="Payroll")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", padx=8, pady=6)
        ttk.Label(bar, text="Month:").pack(side="left")
        ttk.Entry(bar, textvariable=self.month, width=10).pack(side="left", padx=6)
        for text, cmd in [("Run payroll", self.run_payroll), ("Refresh", self.refresh_all),
                          ("Save payslip PDF", self.save_pdf), ("Export CSV", self.export_csv),
                          ("Delete payslip", self.delete_slip)]:
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=3)
        self.pay_tree = make_tree(tab, [
            ("id", "ID", 50, "center"), ("name", "Name", 220, "w"),
            ("gross", "Gross", 120, "e"), ("ded", "Deductions", 120, "e"),
            ("net", "Net pay", 120, "e")])
        self.totals = ttk.Label(tab, text="")
        self.totals.pack(anchor="e", padx=12, pady=(0, 8))

    @guarded
    def run_payroll(self):
        month = core.validate_month(self.month.get().strip())
        adjustments = {}
        if messagebox.askyesno("Adjustments", "Enter bonus / other deductions per employee?"):
            done = {s["emp_id"] for s in core.list_payslips(month)}
            for e in core.list_employees():
                if e["id"] in done:
                    continue
                bonus = simpledialog.askstring("Bonus", f"Bonus / overtime for {e['name']}:",
                                               initialvalue="0", parent=self.root)
                if bonus is None:
                    return
                other = simpledialog.askstring("Deductions", f"Other deductions for {e['name']}:",
                                               initialvalue="0", parent=self.root)
                if other is None:
                    return
                adjustments[e["id"]] = (core.D(bonus, "Bonus"), core.D(other, "Other deductions"))
        processed, skipped = core.run_payroll(month, adjustments)
        notes = [f"{n}: {r}" for n, r in skipped if r != "payslip already exists"]
        messagebox.showinfo("Payroll", f"{len(processed)} payslip(s) created."
                            + ("\n\nSkipped:\n" + "\n".join(notes) if notes else ""))
        self.refresh_all()

    @guarded
    def save_pdf(self):
        emp_id = selected_id(self.pay_tree)
        if not emp_id:
            return
        month = core.validate_month(self.month.get().strip())
        slip = core.get_payslip(emp_id, month)
        path = filedialog.asksaveasfilename(defaultextension=".pdf",
                                            initialfile=f"payslip_{emp_id}_{month}.pdf",
                                            filetypes=[("PDF", "*.pdf")])
        if path:
            with open(path, "wb") as f:
                f.write(build_payslip_pdf(slip, core.COMPANY_NAME))
            messagebox.showinfo("Saved", f"Payslip saved to {path}")

    @guarded
    def export_csv(self):
        month = core.validate_month(self.month.get().strip())
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            initialfile=f"payroll_{month}.csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            with open(path, "w", newline="", encoding="utf-8") as f:
                f.write(core.export_csv(month))
            messagebox.showinfo("Saved", f"Exported to {path}")

    @guarded
    def delete_slip(self):
        emp_id = selected_id(self.pay_tree)
        month = core.validate_month(self.month.get().strip())
        if emp_id and messagebox.askyesno("Delete", "Delete this payslip so it can be re-run?"):
            core.delete_payslip(emp_id, month)
            self.refresh_all()

    # ---- refresh
    def refresh_all(self):
        for tree in (self.emp_tree, self.att_tree, self.pay_tree):
            tree.delete(*tree.get_children())
        employees = core.list_employees()
        for e in employees:
            self.emp_tree.insert("", "end", values=(
                e["id"], e["name"], e["department"], e["designation"],
                money(e["basic"]), money(e["allowances"])))
        try:
            month = core.validate_month(self.month.get().strip())
        except ValueError:
            self.totals.config(text="Enter the month as YYYY-MM")
            return
        att = core.attendance_for_month(month)
        for e in employees:
            wd, lop = att.get(e["id"], (core.DEFAULT_WORKING_DAYS, ZERO))
            self.att_tree.insert("", "end", values=(e["id"], e["name"], wd, lop))
        slips = core.list_payslips(month)
        for s in slips:
            self.pay_tree.insert("", "end", values=(
                s["emp_id"], s["name"], money(s["gross"]), money(s["total_ded"]), money(s["net_pay"])))
        total = sum((s["net_pay"] for s in slips), ZERO)
        self.totals.config(text=f"{len(slips)} payslip(s) - total net pay {money(total)}")


def main():
    core.init_db()
    root = tk.Tk()
    root.withdraw()
    if not login_flow(root):
        root.destroy()
        return
    root.deiconify()
    MainUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
