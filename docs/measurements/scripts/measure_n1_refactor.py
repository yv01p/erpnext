"""Re-runnable measurement: queries + wall-clock for SalesInvoice validation methods.

Usage:
    cd ~/frappe-bench/sites
    ../env/bin/python ~/erpnext/docs/measurements/scripts/measure_n1_refactor.py \\
        --site test_site --output /tmp/measure_<commit>.json

The script reports per-(site, N) tuple: (queries, latency_ms_median) over 5 runs
after 1 warm-up iteration. Both frappe.local.cache and frappe.db.value_cache
are cleared between every iteration (the latter is what frappe.db.get_value's
``cache=True`` populates; without clearing it the pre-refactor warm-up
iteration would silently pre-populate every measured iteration's results).

Note on cwd: frappe.init() resolves sites via a path relative to cwd. The
default sites_path is "." so the script must be invoked from inside the bench's
``sites/`` directory (where ``site_config.json`` lives one level down per site).
This matches how ``bench execute`` arranges things internally.
"""

import argparse
import datetime
import json
import statistics
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import frappe
from frappe.utils import add_days, nowdate

# Namespace prefix for all fixtures created by this script. Picked to avoid
# collision with test fixtures (which use "_Test " / "_T-").
FIXTURE_PREFIX = "_measure_n1_"


@contextmanager
def query_counter():
    counter = {"n": 0}
    original_sql = frappe.db.sql

    def wrapped(*args, **kwargs):
        counter["n"] += 1
        return original_sql(*args, **kwargs)

    frappe.db.sql = wrapped
    try:
        yield counter
    finally:
        frappe.db.sql = original_sql


def clear_cache():
    """Clear caches that would let a pre-refactor cache-warmed iteration silently
    skip the DB. We clear:

    - ``frappe.local.cache`` — Frappe's general per-request cache.
    - ``frappe.db.value_cache`` — the DB instance's per-doctype value cache that
      ``frappe.db.get_value(..., cache=True)`` populates. Site 2's pre-refactor
      code uses ``cache=True``; without this clear, the warm-up iteration would
      populate the cache and all measured iterations would show 0 queries.
    """
    frappe.local.cache = {}
    if hasattr(frappe.db, "value_cache"):
        frappe.db.value_cache.clear()


def measure_method(si, method_name, runs=5, warmup=1):
    """Returns dict with 'queries' (post-warmup count, deterministic) and 'latency_ms'."""
    latencies = []
    queries_observed = None
    method = getattr(si, method_name)

    for i in range(warmup + runs):
        clear_cache()
        with query_counter() as counter:
            t0 = time.perf_counter()
            method()
            t1 = time.perf_counter()
        if i >= warmup:
            latencies.append((t1 - t0) * 1000.0)
            queries_observed = counter["n"]

    return {
        "queries": queries_observed,
        "latency_ms_median": statistics.median(latencies),
        "latency_ms_runs": latencies,
    }


# -----------------------------------------------------------------------------
# Fixture builders. These mirror minimal shapes from
# erpnext/.../test_asset.py:create_asset, test_sales_order.py:make_sales_order,
# test_delivery_note.py:create_delivery_note, and test_timesheet.py:make_timesheet
# but inlined here so the script doesn't have to import test modules (which
# carry heavy ERPNextTestSuite bootstrap side effects).
# -----------------------------------------------------------------------------


def _ensure_assets(n_max):
    """Idempotent get-or-create of n_max submitted Assets. Returns list of names."""
    existing = frappe.get_all(
        "Asset",
        filters={"asset_name": ("like", f"{FIXTURE_PREFIX}asset_%")},
        fields=["name", "asset_name"],
        order_by="asset_name",
    )
    by_label = {row["asset_name"]: row["name"] for row in existing}

    names = []
    for i in range(n_max):
        label = f"{FIXTURE_PREFIX}asset_{i:03d}"
        if label in by_label:
            names.append(by_label[label])
            continue
        asset = frappe.get_doc(
            {
                "doctype": "Asset",
                "asset_name": label,
                "asset_category": "Computers",
                "item_code": "Macbook Pro",
                "company": "_Test Company",
                "purchase_date": "2015-01-01",
                "calculate_depreciation": 0,
                "net_purchase_amount": 100000,
                "purchase_amount": 100000,
                "warehouse": "_Test Warehouse - _TC",
                "available_for_use_date": "2020-06-06",
                "location": "Test Location",
                "asset_owner": "Company",
                "asset_type": "Existing Asset",
                "asset_quantity": 1,
            }
        )
        asset.insert(ignore_permissions=True)
        asset.submit()
        names.append(asset.name)
    return names


def _ensure_sales_orders(n_max):
    """Idempotent get-or-create of n_max submitted Sales Orders.

    Uses ``po_no`` as an idempotency tag (free-text customer-PO field, safe to
    overload here since these fixtures aren't reachable from real workflows).
    """
    existing = frappe.get_all(
        "Sales Order",
        filters={"po_no": ("like", f"{FIXTURE_PREFIX}so_%"), "docstatus": 1},
        fields=["name", "po_no"],
        order_by="po_no",
    )
    by_label = {row["po_no"]: row["name"] for row in existing}

    names = []
    for i in range(n_max):
        label = f"{FIXTURE_PREFIX}so_{i:03d}"
        if label in by_label:
            names.append(by_label[label])
            continue
        so = frappe.new_doc("Sales Order")
        so.company = "_Test Company"
        so.customer = "_Test Customer"
        so.currency = "INR"
        so.transaction_date = nowdate()
        so.delivery_date = add_days(so.transaction_date, 10)
        so.po_no = label
        so.append(
            "items",
            {
                "item_code": "_Test Item",
                "warehouse": "_Test Warehouse - _TC",
                "qty": 1,
                "rate": 100,
            },
        )
        so.insert(ignore_permissions=True)
        so.submit()
        names.append(so.name)
    return names


def _ensure_delivery_notes(n_max):
    """Idempotent get-or-create of n_max submitted Delivery Notes."""
    existing = frappe.get_all(
        "Delivery Note",
        filters={"po_no": ("like", f"{FIXTURE_PREFIX}dn_%"), "docstatus": 1},
        fields=["name", "po_no"],
        order_by="po_no",
    )
    by_label = {row["po_no"]: row["name"] for row in existing}

    names = []
    for i in range(n_max):
        label = f"{FIXTURE_PREFIX}dn_{i:03d}"
        if label in by_label:
            names.append(by_label[label])
            continue
        dn = frappe.new_doc("Delivery Note")
        dn.company = "_Test Company"
        dn.customer = "_Test Customer"
        dn.currency = "INR"
        dn.posting_date = nowdate()
        dn.po_no = label
        dn.append(
            "items",
            {
                "item_code": "_Test Item",
                "warehouse": "_Test Warehouse - _TC",
                "qty": 1,
                "rate": 100,
                "cost_center": "_Test Cost Center - _TC",
                "expense_account": "Cost of Goods Sold - _TC",
                "allow_zero_valuation_rate": 1,
                "conversion_factor": 1.0,
            },
        )
        dn.insert(ignore_permissions=True)
        dn.submit()
        names.append(dn.name)
    return names


def _ensure_timesheets(n_max, employee):
    """Idempotent get-or-create of n_max submitted Timesheets, each with one detail row.

    Returns (timesheet_names, timesheet_detail_names) — same length, parallel lists.
    Idempotency tag is stored in the Timesheet's ``note`` field.
    Each timesheet uses a distinct from_time to avoid OverlapError.
    """
    existing = frappe.get_all(
        "Timesheet",
        filters={"note": ("like", f"{FIXTURE_PREFIX}ts_%"), "docstatus": 1},
        fields=["name", "note"],
        order_by="note",
    )
    by_label = {row["note"]: row["name"] for row in existing}

    sheet_names = []
    detail_names = []
    # Anchor base time well in the past to avoid colliding with real timesheets.
    base = datetime.datetime(2024, 1, 1, 9, 0, 0)
    for i in range(n_max):
        label = f"{FIXTURE_PREFIX}ts_{i:03d}"
        if label in by_label:
            ts_name = by_label[label]
        else:
            ts = frappe.new_doc("Timesheet")
            ts.employee = employee
            ts.company = "_Test Company"
            ts.note = label
            from_time = base + datetime.timedelta(hours=i * 3)
            to_time = from_time + datetime.timedelta(hours=2)
            ts.append(
                "time_logs",
                {
                    "is_billable": 1,
                    "activity_type": "_Test Activity Type",
                    "from_time": from_time,
                    "to_time": to_time,
                    "hours": 2,
                },
            )
            ts.insert(ignore_permissions=True)
            ts.submit()
            ts_name = ts.name
        sheet_names.append(ts_name)
        # Fetch the (single) detail row name for this sheet.
        detail = frappe.db.get_value(
            "Timesheet Detail", {"parent": ts_name}, "name"
        )
        detail_names.append(detail)
    return sheet_names, detail_names


def ensure_fixtures(n_max):
    """Idempotent: returns dict of fixture name lists, each of length n_max."""
    # Use a stable existing employee. The seeded test employees are present on
    # any ERPNext test_site; we don't create our own to avoid User-creation churn.
    employee = frappe.db.get_value(
        "Employee", {"status": "Active", "company": "_Test Company"}, "name"
    )
    if not employee:
        raise RuntimeError(
            "No active Employee on _Test Company; this script requires seeded test data."
        )

    assets = _ensure_assets(n_max)
    sales_orders = _ensure_sales_orders(n_max)
    delivery_notes = _ensure_delivery_notes(n_max)
    timesheets, timesheet_details = _ensure_timesheets(n_max, employee)
    frappe.db.commit()  # persist fixtures across runs

    return {
        "assets": assets,
        "sales_orders": sales_orders,
        "delivery_notes": delivery_notes,
        "timesheets": timesheets,
        "timesheet_details": timesheet_details,
    }


# -----------------------------------------------------------------------------
# Per-site SI builders. Each returns an UNSAVED Sales Invoice with N items
# wired to point at the supplied fixture rows. We never insert these — they
# only exist long enough for measure_method() to call the target validation.
# -----------------------------------------------------------------------------


def _new_unsaved_si():
    """Mirror of test_sales_invoice.create_sales_invoice with do_not_save=1 and
    no items appended (caller does that)."""
    si = frappe.new_doc("Sales Invoice")
    si.posting_date = nowdate()
    si.company = "_Test Company"
    si.customer = "_Test Customer"
    si.debit_to = "Debtors - _TC"
    si.currency = "INR"
    si.conversion_rate = 1
    si.naming_series = "T-SINV-"
    return si


def build_site1_invoice(n, asset_names):
    """Unsaved SI with N fixed-asset items pointing at the supplied submitted Assets."""
    if n > len(asset_names):
        raise ValueError(f"need {n} assets, only have {len(asset_names)}")
    si = _new_unsaved_si()
    for i in range(n):
        si.append(
            "items",
            {
                "item_code": "Macbook Pro",
                "item_name": "Macbook Pro",
                "description": "Macbook Pro",
                "warehouse": "_Test Warehouse - _TC",
                "qty": 1,
                "uom": "Nos",
                "stock_uom": "Nos",
                "rate": 90000,
                "income_account": "Sales - _TC",
                "expense_account": "Cost of Goods Sold - _TC",
                "asset": asset_names[i],
                "is_fixed_asset": 1,
                "cost_center": "_Test Cost Center - _TC",
                "conversion_factor": 1,
            },
        )
    return si


def build_site2_invoice(n, so_names, dn_names):
    """Unsaved SI with N items each referencing a distinct submitted SO and DN."""
    if n > len(so_names) or n > len(dn_names):
        raise ValueError(f"need {n} SOs/DNs, only have {len(so_names)}/{len(dn_names)}")
    si = _new_unsaved_si()
    for i in range(n):
        si.append(
            "items",
            {
                "item_code": "_Test Item",
                "item_name": "_Test Item",
                "description": "_Test Item",
                "warehouse": "_Test Warehouse - _TC",
                "qty": 1,
                "uom": "Nos",
                "stock_uom": "Nos",
                "rate": 100,
                "income_account": "Sales - _TC",
                "expense_account": "Cost of Goods Sold - _TC",
                "cost_center": "_Test Cost Center - _TC",
                "conversion_factor": 1,
                "sales_order": so_names[i],
                "delivery_note": dn_names[i],
            },
        )
    return si


def build_site3_invoice(n, timesheet_names, detail_names):
    """Unsaved SI with N timesheet entries (time_sheet + timesheet_detail set)."""
    if n > len(timesheet_names) or n > len(detail_names):
        raise ValueError(
            f"need {n} timesheets/details, only have "
            f"{len(timesheet_names)}/{len(detail_names)}"
        )
    si = _new_unsaved_si()
    # Site 3 still needs at least one item to be a valid SI shape; the timesheet
    # validation only reads self.timesheets so the items are inert.
    si.append(
        "items",
        {
            "item_code": "_Test Item",
            "item_name": "_Test Item",
            "description": "_Test Item",
            "warehouse": "_Test Warehouse - _TC",
            "qty": 1,
            "uom": "Nos",
            "stock_uom": "Nos",
            "rate": 100,
            "income_account": "Sales - _TC",
            "expense_account": "Cost of Goods Sold - _TC",
            "cost_center": "_Test Cost Center - _TC",
            "conversion_factor": 1,
        },
    )
    for i in range(n):
        si.append(
            "timesheets",
            {
                "time_sheet": timesheet_names[i],
                "timesheet_detail": detail_names[i],
                "billing_hours": 2,
                "billing_amount": 100,
            },
        )
    return si


def run_all_sites(site, output_path):
    frappe.init(site=site)
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        fixtures = ensure_fixtures(n_max=20)
        results = []
        for n in (1, 5, 20):
            si1 = build_site1_invoice(n, fixtures["assets"])
            results.append({"site": 1, "method": "validate_fixed_asset", "n": n,
                            **measure_method(si1, "validate_fixed_asset")})
            si2 = build_site2_invoice(n, fixtures["sales_orders"], fixtures["delivery_notes"])
            results.append({"site": 2, "method": "check_prev_docstatus", "n": n,
                            **measure_method(si2, "check_prev_docstatus")})
            si3 = build_site3_invoice(n, fixtures["timesheets"], fixtures["timesheet_details"])
            results.append({"site": 3, "method": "validate_time_sheets_are_submitted", "n": n,
                            **measure_method(si3, "validate_time_sheets_are_submitted")})

        erpnext_root = Path(__file__).resolve().parents[3]
        commit = subprocess.check_output(
            ["git", "-C", str(erpnext_root), "rev-parse", "HEAD"], text=True
        ).strip()
        payload = {"commit": commit, "results": results}
        Path(output_path).write_text(json.dumps(payload, indent=2))
        print(f"Wrote {output_path} ({len(results)} rows)")
    finally:
        frappe.destroy()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run_all_sites(args.site, args.output)
