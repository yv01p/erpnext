"""Re-runnable measurement: queries + wall-clock for Subcontracting validate() methods.

Measures both cold and warm cache paths. Fixture names use the `_measure_part3_` prefix
to avoid collision with Part 2's lingering `_measure_n1_` fixtures in test_site.

Usage:
    cd ~/frappe-bench
    env/bin/python ~/erpnext/docs/measurements/scripts/measure_part3_refactor.py \
        --site test_site --output /tmp/measure_part3_<label>.json

Per (subject, N) tuple, reports cold and warm:
- queries (post-warmup deterministic count)
- latency_ms median over 5 runs after 1 warm-up iteration

Note on builders: existing test helpers provide `create_subcontracting_order(**args)` at
test_subcontracting_order.py:826. Receipt builds via the order's `make_subcontracting_receipt`
mapper. Inward order builds via `frappe.get_doc({"doctype": "Subcontracting Inward Order", ...})`
composing from `create_so_scio` and `make_subcontracted_items` in
test_subcontracting_inward_order.py. The builders below are NotImplementedError stubs;
the implementer fills them at script-write time after surveying the existing test helpers.
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import frappe

FIXTURE_PREFIX = "_measure_part3_"


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
    """Clear BOTH the per-request cache (frappe.local.cache) AND the DB-instance value_cache.
    Without clearing both, frappe.db.get_value(..., cache=True) callsites stay hit-warm."""
    frappe.local.cache = {}
    if hasattr(frappe.db, "value_cache"):
        frappe.db.value_cache.clear()


def measure_method(doc, method_name, runs=5, warmup=1):
    """Returns dict with cold + warm measurements."""
    method = getattr(doc, method_name)

    # Cold path: clear caches between every iteration
    cold_latencies = []
    cold_queries = None
    for i in range(warmup + runs):
        clear_cache()
        with query_counter() as counter:
            t0 = time.perf_counter()
            method()
            t1 = time.perf_counter()
        if i >= warmup:
            cold_latencies.append((t1 - t0) * 1000.0)
            cold_queries = counter["n"]

    # Warm path: clear cache ONCE upfront, then never again
    warm_latencies = []
    warm_queries = None
    clear_cache()
    for i in range(warmup + runs):
        with query_counter() as counter:
            t0 = time.perf_counter()
            method()
            t1 = time.perf_counter()
        if i >= warmup:
            warm_latencies.append((t1 - t0) * 1000.0)
            warm_queries = counter["n"]

    return {
        "queries_cold": cold_queries,
        "queries_warm": warm_queries,
        "latency_ms_cold_median": statistics.median(cold_latencies),
        "latency_ms_warm_median": statistics.median(warm_latencies),
        "latency_ms_cold_runs": cold_latencies,
        "latency_ms_warm_runs": warm_latencies,
    }


# -----------------------------------------------------------------------------
# Fixture builders. We reuse the existing inward-order test seed (Basic FG Item +
# Basic RM + Service Items 1-4 + BOMs + Subcontracting BOMs + Customer Warehouse)
# for the SCIO measurement (it only needs 1 service-item pair per N), and create
# our own `_measure_part3_svc_*` + `_measure_part3_fg_*` + `_measure_part3_rm`
# items with per-FG BOMs for the SCO/SCR measurements which need up to N=20
# DISTINCT service items per PO (PO validator rejects duplicate item rows).
# -----------------------------------------------------------------------------


def _ensure_seed_fixtures():
    """Run the idempotent inward-order seed helper (create_test_data) which provides
    Basic FG Item, Basic RM, Service Items 1-4, BOMs, Subcontracting BOMs, and the
    customer warehouse. Plus create our own per-script Items + BOMs for the SCO/SCR
    measurements that need up to N=20 distinct items per parent doc."""
    from erpnext.manufacturing.doctype.production_plan.test_production_plan import make_bom
    from erpnext.stock.doctype.item.test_item import make_item
    from erpnext.subcontracting.doctype.subcontracting_inward_order.test_subcontracting_inward_order import (
        create_test_data as create_inward_test_data,
    )

    create_inward_test_data()

    # One shared raw material for our per-script BOMs. Customer-provided so the SCIO's
    # validate_customer_provided_items check passes (it requires at least one CPI per FG).
    rm_name = f"{FIXTURE_PREFIX}rm"
    if not frappe.db.exists("Item", rm_name):
        make_item(
            rm_name,
            {"is_stock_item": 1, "is_purchase_item": 0, "is_customer_provided_item": 1, "valuation_rate": 10},
        )
    else:
        # Idempotent flag-fix in case a prior run created the item without the CPI flag.
        if not frappe.db.get_value("Item", rm_name, "is_customer_provided_item"):
            frappe.db.set_value("Item", rm_name, "is_customer_provided_item", 1)
            frappe.db.set_value("Item", rm_name, "is_purchase_item", 0)

    # 20 distinct (svc, fg, bom) triples for our per-script PO rows.
    for i in range(20):
        svc = f"{FIXTURE_PREFIX}svc_{i:02d}"
        fg = f"{FIXTURE_PREFIX}fg_{i:02d}"
        if not frappe.db.exists("Item", svc):
            make_item(svc, {"is_stock_item": 0})
        if not frappe.db.exists("Item", fg):
            make_item(fg, {"is_stock_item": 1, "is_sub_contracted_item": 1})
        if not frappe.db.exists("BOM", {"item": fg}):
            make_bom(item=fg, raw_materials=[rm_name], rate=100, currency="INR", set_as_default_bom=1)


def _ensure_submitted_po(n, idempotency_tag):
    """Idempotent get-or-create of one submitted, subcontracted Purchase Order with N
    service-item lines. Returns the PO name.

    Tagged via `title` (Data field) so we can find it across runs. If a prior run's PO
    is now fully received (per_received == 100), build a fresh one with a unique tag."""
    from erpnext.buying.doctype.purchase_order.test_purchase_order import create_purchase_order

    # Look for an existing usable PO with this tag (docstatus=1, per_received<100).
    existing = frappe.get_all(
        "Purchase Order",
        filters={
            "title": ("like", f"{idempotency_tag}%"),
            "docstatus": 1,
            "per_received": ("<", 100),
        },
        fields=["name"],
        order_by="creation desc",
        limit=1,
    )
    if existing:
        return existing[0]["name"]

    # Build fresh — vary the tag suffix by timestamp so multiple fresh ones don't collide.
    fresh_tag = f"{idempotency_tag}_{int(time.time())}"
    service_items = []
    for i in range(n):
        # Use our per-script distinct (svc, fg) pairs to avoid PO's "Same item cannot
        # be entered multiple times" validator. ensure_fixtures created 20 such pairs.
        fg = f"{FIXTURE_PREFIX}fg_{i:02d}"
        bom_name = frappe.db.get_value("BOM", {"item": fg, "is_default": 1}, "name")
        service_items.append(
            {
                "warehouse": "_Test Warehouse - _TC",
                "item_code": f"{FIXTURE_PREFIX}svc_{i:02d}",
                "qty": 1,
                "rate": 100,
                "fg_item": fg,
                "fg_item_qty": 1,
                "bom": bom_name,
            }
        )
    po = create_purchase_order(
        rm_items=service_items,
        is_subcontracted=1,
        supplier_warehouse="_Test Warehouse 1 - _TC",
    )
    po.db_set("title", fresh_tag)
    return po.name


def _ensure_submitted_sales_order(n, idempotency_tag):
    """Idempotent get-or-create of one submitted, is_subcontracted=1 Sales Order with
    N items. Returns the SO name. Tagged via po_no."""
    from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order

    existing = frappe.get_all(
        "Sales Order",
        filters={
            "po_no": ("like", f"{idempotency_tag}%"),
            "docstatus": 1,
        },
        fields=["name"],
        order_by="creation desc",
        limit=1,
    )
    if existing:
        return existing[0]["name"]

    fresh_tag = f"{idempotency_tag}_{int(time.time())}"
    # Use our per-script distinct (svc, fg) pairs (20 available) for the SCIO too.
    # SO with is_subcontracted=1 needs Service Items as line items + fg_item per row.
    item_list = []
    for i in range(n):
        item_list.append(
            {
                "item_code": f"{FIXTURE_PREFIX}svc_{i:02d}",
                "qty": 5,
                "fg_item": f"{FIXTURE_PREFIX}fg_{i:02d}",
                "fg_item_qty": 5,
            }
        )
    so = make_sales_order(is_subcontracted=1, item_list=item_list, po_no=fresh_tag)
    return so.name


def ensure_fixtures(n_max):
    """Idempotent: returns dict with per-N PO + SO names for each N in (1, 5, 20)."""
    _ensure_seed_fixtures()

    pos = {}
    sos = {}
    for n in (1, 5, 20):
        pos[n] = _ensure_submitted_po(n, f"{FIXTURE_PREFIX}po_n{n}")
        sos[n] = _ensure_submitted_sales_order(n, f"{FIXTURE_PREFIX}so_n{n}")

    frappe.db.commit()  # persist fixtures across runs
    return {"purchase_orders": pos, "sales_orders": sos}


def build_subcontracting_order(n, fixtures):
    """Build (unsaved) one Subcontracting Order with N items from the pre-seeded
    submitted PO for this N. Reuses `create_subcontracting_order(do_not_save=1)`
    from test_subcontracting_order.py:826 which internally calls
    `get_mapped_subcontracting_order(source_name=po_name)`."""
    from erpnext.subcontracting.doctype.subcontracting_order.test_subcontracting_order import (
        create_subcontracting_order,
    )

    po_name = fixtures["purchase_orders"][n]
    sco = create_subcontracting_order(po_name=po_name, do_not_save=1)
    # validate() expects items[].conversion_factor to be set (normally done by
    # before_validate). Since we call validate() directly here, call before_validate
    # once at build time so it doesn't pollute the measurement.
    sco.before_validate()
    # Caller will invoke validate() repeatedly; for SCR builder we need a saved+submitted
    # SCO. We return the unsaved doc here for SCO.validate measurement, and the
    # caller separately constructs a submitted-SCO when building the SCR.
    return sco


def build_subcontracting_receipt(n, fixtures, sco_name):
    """Build (unsaved) one Subcontracting Receipt from a submitted SCO. Composes via
    `make_subcontracting_receipt(source_name)` (mapper in subcontracting_order.py:432).
    Returns the unsaved SCR doc.

    Note: SCO must be already submitted (sco_name is the name of a submitted SCO).
    Caller is responsible for submitting the SCO before invoking this builder."""
    from erpnext.subcontracting.doctype.subcontracting_order.subcontracting_order import (
        make_subcontracting_receipt,
    )

    scr = make_subcontracting_receipt(sco_name)
    scr.before_validate()
    return scr


def build_subcontracting_inward_order(n, fixtures):
    """Build (unsaved) one Subcontracting Inward Order with N items from the pre-seeded
    submitted SO for this N. Maps via
    `make_subcontracting_inward_order(source_name)` from sales_order.py:2074."""
    from erpnext.selling.doctype.sales_order.sales_order import make_subcontracting_inward_order

    so_name = fixtures["sales_orders"][n]
    scio = make_subcontracting_inward_order(so_name)
    # Mapper requires populate_items_table post_process which already ran inside the
    # mapper. Provide delivery warehouse like create_so_scio does so validate passes.
    for item in scio.items:
        if not item.get("delivery_warehouse"):
            item.delivery_warehouse = "_Test Warehouse - _TC"
    scio.before_validate()
    return scio


def _build_and_submit_sco_for_receipt(n, fixtures):
    """Helper: build + insert + submit a fresh SCO so the SCR builder has a submitted
    parent. We need a fresh PO for this (since the script-managed PO is reserved for
    SCO.validate measurement and might already be linked to a saved SCO across runs).

    Strategy: build a one-shot PO + SCO solely for the SCR measurement. Tag the PO
    with `_measure_part3_po_for_scr_n{n}_<timestamp>` so it's distinct from the
    SCO-validate PO."""
    po_name = _ensure_submitted_po(n, f"{FIXTURE_PREFIX}po_for_scr_n{n}")
    from erpnext.subcontracting.doctype.subcontracting_order.test_subcontracting_order import (
        create_subcontracting_order,
    )

    sco = create_subcontracting_order(po_name=po_name)  # inserts + submits
    return sco.name


def run_all(site, output_path):
    frappe.init(site=site)
    frappe.connect()
    try:
        fixtures = ensure_fixtures(n_max=20)
        results = []
        for n in (1, 5, 20):
            sco = build_subcontracting_order(n, fixtures)
            results.append({"subject": "SubcontractingOrder.validate", "n": n,
                            **measure_method(sco, "validate")})

            # SCR needs a separately-submitted SCO as parent.
            sco_for_scr = _build_and_submit_sco_for_receipt(n, fixtures)
            scr = build_subcontracting_receipt(n, fixtures, sco_for_scr)
            results.append({"subject": "SubcontractingReceipt.validate", "n": n,
                            **measure_method(scr, "validate")})

            scio = build_subcontracting_inward_order(n, fixtures)
            results.append({"subject": "SubcontractingInwardOrder.validate", "n": n,
                            **measure_method(scio, "validate")})

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
    run_all(args.site, args.output)
