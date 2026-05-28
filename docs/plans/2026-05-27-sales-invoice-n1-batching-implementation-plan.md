# SalesInvoice N+1 Batching Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Source spec:** `docs/specs/2026-05-27-sales-invoice-n1-batching-design.md` (commit SHA: `db7f4a669f2566ea3061c02e54533ee3556ca6b4`)

**Goal:** Eliminate 5 read-N+1 patterns across 3 validation methods in `erpnext/accounts/doctype/sales_invoice/sales_invoice.py` via the standard collect-then-batch pattern; capture before/after query counts and wall-clock latency via a re-runnable measurement script; produce a lesson citing measured numbers.

**Architecture:** Three `SalesInvoice` methods modified in place. Each method's per-row `frappe.db.get_value` call is replaced by a single pre-loop `frappe.db.get_all(..., filters={"name": ("in", list)}, ...)` building a `{name: value}` dict; the existing loop body dereferences from the dict instead of querying per iteration. No new methods, no new imports.

**Tech stack:** Python 3.10+ (ruff `target_version = "py310"`), Frappe v16 framework (`frappe.db.get_all`, `frappe.throw`, `_`), MariaDB via Frappe Bench (`~/frappe-bench/`).

---

## File Structure

**Modify:**
- `erpnext/accounts/doctype/sales_invoice/sales_invoice.py` — three methods: `validate_fixed_asset` (L404), `check_prev_docstatus` (L1391), `validate_time_sheets_are_submitted` (L875).
- `erpnext/accounts/doctype/sales_invoice/test_sales_invoice.py` — conditional, per-site, 0-1 new test methods depending on per-site coverage audit at implementation time.

**Create:**
- `docs/measurements/` and `docs/measurements/scripts/` — new directories.
- `docs/measurements/scripts/measure_n1_refactor.py` — re-runnable measurement script.
- `docs/measurements/02-sales-invoice-n1-batching.md` — measurement report.
- `docs/lessons/02-intermediate-sales-invoice-n1s.md` — pedagogical write-up.

## Inherited from spec

The following assumptions were verified by `thorough-brainstorming` at spec-write time and are NOT re-verified here. Trusted as ground truth:

- **A1-A3**: Exact line numbers and loop structure of Site 1 (`validate_fixed_asset` at `sales_invoice.py:404`, get_value at `:412`); loop iterates `self.get("items")`; only `Asset.status` is read.
- **A4-A9 (re-scope)**: Arch-review's original sites at L728/L740 are load-modify-save, not read-N+1. User re-scoped to `check_prev_docstatus:1391` and `validate_time_sheets_are_submitted:875`. Plan implements the re-scoped sites only.
- **A10**: `frappe.db.get_all(doctype, filters={"name": ("in", list)}, fields=[...])` returns list-of-dicts, used by warm-up at `payment_entry.py:834-840`.
- **A11**: `frappe.db.get_all` and `frappe.db.get_value` apply the same permission scope for internal system callers (both bypass User Permissions).
- **A13**: All 3 sites are pure reads — no row-level locks, no `for_update`, no mid-loop mutations.
- **A14**: Loops are 12-20 lines, single-concern; inline 2-pass is the right shape (no helper extraction).
- **A15-A19**: `test_sales_invoice.py` exists; test class is `TestSalesInvoice(ERPNextTestSuite)` at L53; `bench run-tests --module ... --lightmode` is the correct invocation; same site/apps/bootstrap as warm-up; grep-able for coverage audits.
- **A20**: The 3 methods are validation methods called from `validate()`/`on_submit()`; no API contracts on per-iteration timing.
- **A21**: No existing tests mock `frappe.db.get_value` for the 5 affected doctypes.
- **A22**: Exception messages identical pre/post — the same `frappe.throw(_(...))` calls fire with the same template strings.
- **A23**: Arch review's line numbers reflect current `sales_invoice.py` state on `refactoring`.
- **A24**: The 3 sites are in different methods → independently committable.
- **AM1-AM5**: `frappe.db.sql` is monkey-patchable (bound method on the `Database` instance); `time.perf_counter()` has nanosecond resolution; Sales Invoices with N items via existing fixture helpers + `si.append("items", {...})` is tractable; the 3 target methods can be invoked directly on a populated `si` doc without full submit; checkout dance for before/after is feasible.

## Verified plan-level assumptions

Newly introduced by this plan (paths, signatures, commands, ordering, consumer impact) and verified at plan-write time:

| # | Category | Assumption | Evidence |
|---|----------|------------|----------|
| P1 | File path (new) | `docs/measurements/` does NOT exist; `docs/measurements/scripts/` does not exist | `ls -la docs/measurements/` returned "No such file or directory". Task 4 must `mkdir -p`. |
| P2 | File path (new) | No existing `docs/lessons/02-*` or `docs/measurements/02-*` file | `ls docs/lessons/02* docs/measurements/02*` returned "No such file or directory". Safe to create. |
| P3 | File path (existing) | `docs/lessons/01-warm-up-update-payment-schedule.md` exists; lesson 02 mirrors its shape (Title → commits/refs → "What we found" → "How we identified it" → "What we did" → measurement results → "Lessons") | File read; 18,705 bytes; structure confirmed |
| P4 | Command | `~/.local/bin/bench` exists and is executable | `ls -la ~/.local/bin/bench` → executable, 209 bytes |
| P5 | Command | `bench --site test_site run-tests --module <X> --lightmode` syntax is correct (`--site` before subcommand; `--module` + `--lightmode` are valid flags) | `cd ~/frappe-bench && ~/.local/bin/bench --site test_site run-tests --help` lists `--module TEXT` and `--lightmode` flags |
| P6 | Command (commit msg) | `<type>(<scope>): <subject>` convention is in use | `git log --oneline -20` shows `refactor(payment_entry):`, `test(payment_entry):`, `fix(sales_invoice):`, scope-less `docs:`, `chore:`. Plan uses `refactor(sales_invoice):` for Tasks 1-3 and `docs:` for Tasks 4-5. |
| P7 | Function signature | `create_sales_invoice(**args)` at `test_sales_invoice.py:4870` builds one Sales Invoice with one items row; supports `do_not_save=1` for in-memory builds; accepts `asset` field in the appended item dict | Read of L4870-4963 |
| P8 | API | `frappe.local.cache = {}` is the canonical clear (resets per-request cache backing `cache=True`) | `frappe/cache_manager.py:298` uses this exact assignment |
| P9 | API | Self-bootstrap idiom: `frappe.init(site=site); frappe.connect(); try: ... finally: frappe.destroy()` | Verified at `frappe/commands/site.py:630-640` (`_fetch_table_stats` command) |
| P10 | Code-in-plan validity | Warm-up's batched pattern at `payment_entry.py:834-840` exactly matches `frappe.db.get_all(<doctype>, filters={"name": ("in", list(names))}, fields=["name", "<field>"])` | Direct read of L820-840; verbatim shape |
| P11 | Consumer impact | The 3 methods are called only from `sales_invoice.py:318/362/457` (the `validate()` chain on SalesInvoice). **POSInvoice (`pos_invoice.py:30`) inherits from SalesInvoice and does NOT override any of the 3 methods** — refactor applies transparently to POSInvoice instances via inheritance. No other callers. `Item.validate_fixed_asset` and `PurchaseInvoice.check_prev_docstatus` are unrelated methods on different classes with the same name. | `grep -rn "validate_fixed_asset\|check_prev_docstatus\|validate_time_sheets_are_submitted" --include="*.py" erpnext/` |
| P12 | Task ordering | Tasks 1-3 modify different methods in the same file, no shared symbols; can be reviewed independently. SDD must execute them **sequentially** (parallel subagents would race on `sales_invoice.py` edits). Task 4 depends on Tasks 1-3 committed (the AFTER tree state). Task 5 depends on Task 4 (cites measured numbers). | Direct reasoning over file shape |
| P13 | Empty `in` list | `frappe.db.get_all(filters={"name": ("in", [])}, ...)` behavior is not fully documented internally; **plan adopts explicit `if names:` guards at every site rather than depending on Frappe's empty-list handling**. Site 1's `if not self.is_return:` doubles as a guard. | `~/frappe-bench/apps/frappe/frappe/database/query.py:1870-1876` handles NULL coalescing for empty `in` lists but actual SQL emission for empty is not the path we want to depend on. Guarding is defensive and matches spec's "or skip empty sets" prose for Site 2. |
| P14 | Pre-refactor test baseline | No pre-existing green baseline for `test_sales_invoice` module (warm-up only smoked `test_payment_entry`). **Task 1 Step 1 establishes the baseline before any edit; if red, halt and surface.** | Inferred: `git log` shows no `sales_invoice.py` or `test_sales_invoice.py` changes since warm-up; warm-up's payment_entry suite was green at `ec1a75f2e7`. |

## Tasks

### Task 1: Refactor Site 1 — `validate_fixed_asset` (Asset.status N+1)

**Files:**
- Modify: `erpnext/accounts/doctype/sales_invoice/sales_invoice.py:404-429`
- Test (conditional): `erpnext/accounts/doctype/sales_invoice/test_sales_invoice.py`

- [ ] **Step 1: Establish baseline.** Run the sales_invoice test module before any edit. If red, HALT and surface the failure — do not proceed.
```bash
cd ~/frappe-bench && ~/.local/bin/bench --site test_site run-tests --module erpnext.accounts.doctype.sales_invoice.test_sales_invoice --lightmode
```
Expected: all tests pass. Record the green count.

- [ ] **Step 2: Audit existing test coverage of `validate_fixed_asset`.** Grep `test_sales_invoice.py` for tests whose execution path passes through this method (most likely indirect — submit/validate flows of Sales Invoices with fixed-asset items). Decision rule:
  - If existing tests exercise the loop with ≥2 distinct fixed-asset rows AND assert post-loop state (no exception, correct asset_status check) → no new test.
  - If existing tests cover happy-path / single-row only → add one targeted test exercising the loop with ≥2 fixed-asset rows.
  - If no test reaches the method → add a targeted test.

```bash
grep -nE "validate_fixed_asset|is_fixed_asset|asset_status" erpnext/accounts/doctype/sales_invoice/test_sales_invoice.py
```

- [ ] **Step 3: (Conditional) Add new test.** If the audit calls for one, write it now in `test_sales_invoice.py` near the existing fixed-asset tests. Test must pre-refactor pass against the still-N+1 code (this is coverage gap-fill, not a behavior change). Run it standalone via:
```bash
cd ~/frappe-bench && ~/.local/bin/bench --site test_site run-tests --module erpnext.accounts.doctype.sales_invoice.test_sales_invoice --case <NewTestCaseName> --lightmode
```

- [ ] **Step 4: Apply the refactor.** Replace `sales_invoice.py:404-429` with the inline 2-pass shape. Key shape:

```python
def validate_fixed_asset(self):
    if self.doctype != "Sales Invoice":
        return

    asset_status_map = {}
    if not self.is_return:
        asset_names = {d.asset for d in self.get("items") if d.is_fixed_asset and d.asset}
        if asset_names:
            asset_status_map = {
                r["name"]: r["status"]
                for r in frappe.db.get_all(
                    "Asset",
                    filters={"name": ("in", list(asset_names))},
                    fields=["name", "status"],
                )
            }

    for d in self.get("items"):
        if d.is_fixed_asset:
            if d.asset:
                if not self.is_return:
                    asset_status = asset_status_map.get(d.asset)
                    if self.update_stock:
                        frappe.throw(_("'Update Stock' cannot be checked for fixed asset sale"))

                    elif asset_status in ("Scrapped", "Cancelled", "Capitalized"):
                        frappe.throw(
                            _("Row #{0}: Asset {1} cannot be sold, it is already {2}").format(
                                d.idx, d.asset, asset_status
                            )
                        )
                    # ... rest of the existing if-elif chain unchanged ...
                elif not self.return_against:
                    # ... unchanged ...
            else:
                # ... unchanged ...
```

Note: the implementer must preserve the rest of the existing if-elif chain verbatim from the current L412-429 — only the `asset_status = frappe.db.get_value(...)` line at L412 is replaced. All `frappe.throw` calls fire under the same conditions with the same arguments. The dict's `.get(d.asset)` returns `None` for items where the Asset row wasn't fetched (which only happens for `self.is_return` invoices, where the asset_status branch is unreachable anyway).

- [ ] **Step 5: Run the sales_invoice test module.** Must be green; same command as Step 1.

- [ ] **Step 6: Commit.**
```bash
git add erpnext/accounts/doctype/sales_invoice/sales_invoice.py
# add test_sales_invoice.py only if Step 3 added a test
git commit -m "refactor(sales_invoice): batch Asset.status lookup in validate_fixed_asset"
```

### Task 2: Refactor Site 2 — `check_prev_docstatus` (SO + DN docstatus N+1s)

**Files:**
- Modify: `erpnext/accounts/doctype/sales_invoice/sales_invoice.py:1391-1403`
- Test (conditional): `erpnext/accounts/doctype/sales_invoice/test_sales_invoice.py`

- [ ] **Step 1: Audit existing test coverage of `check_prev_docstatus`.**
```bash
grep -nE "check_prev_docstatus|sales_order.*docstatus|delivery_note.*docstatus" erpnext/accounts/doctype/sales_invoice/test_sales_invoice.py
```
Apply the same per-site decision rule as Task 1 Step 2.

- [ ] **Step 2: (Conditional) Add new test** if Step 1 calls for one. Test must pre-refactor pass against the still-N+1 code.

- [ ] **Step 3: Apply the refactor.** Replace `sales_invoice.py:1391-1403`:

```python
def check_prev_docstatus(self):
    items = self.get("items")
    so_names = {d.sales_order for d in items if d.sales_order}
    dn_names = {d.delivery_note for d in items if d.delivery_note}

    so_docstatus_map = {}
    if so_names:
        so_docstatus_map = {
            r["name"]: r["docstatus"]
            for r in frappe.db.get_all(
                "Sales Order",
                filters={"name": ("in", list(so_names))},
                fields=["name", "docstatus"],
            )
        }

    dn_docstatus_map = {}
    if dn_names:
        dn_docstatus_map = {
            r["name"]: r["docstatus"]
            for r in frappe.db.get_all(
                "Delivery Note",
                filters={"name": ("in", list(dn_names))},
                fields=["name", "docstatus"],
            )
        }

    for d in items:
        if d.sales_order and so_docstatus_map.get(d.sales_order) != 1:
            frappe.throw(_("Sales Order {0} is not submitted").format(d.sales_order))

        if d.delivery_note and dn_docstatus_map.get(d.delivery_note) != 1:
            throw(_("Delivery Note {0} is not submitted").format(d.delivery_note))
```

Note: the original at L1395/L1401 uses `frappe.db.get_value(..., cache=True)`. The batched version queries fresh once per request. Within a single request, the observed value is identical (cache hits return whatever the first miss wrote, which equals the batched fresh read). The `throw` call at L1403 uses bare `throw` (imported from `frappe`); preserve that import — verify it's imported at the top of the file, and if not, use `frappe.throw` for consistency with L1397.

- [ ] **Step 4: Run the sales_invoice test module.** Must be green.

- [ ] **Step 5: Commit.**
```bash
git add erpnext/accounts/doctype/sales_invoice/sales_invoice.py
# add test_sales_invoice.py only if Step 2 added a test
git commit -m "refactor(sales_invoice): batch docstatus lookups in check_prev_docstatus"
```

### Task 3: Refactor Site 3 — `validate_time_sheets_are_submitted` (Timesheet Detail + Timesheet N+1s)

**Files:**
- Modify: `erpnext/accounts/doctype/sales_invoice/sales_invoice.py:875-895`
- Test (conditional): `erpnext/accounts/doctype/sales_invoice/test_sales_invoice.py`

- [ ] **Step 1: Audit existing test coverage of `validate_time_sheets_are_submitted`.**
```bash
grep -nE "validate_time_sheets_are_submitted|timesheet.*invoice|TimesheetDetail" erpnext/accounts/doctype/sales_invoice/test_sales_invoice.py
```
Apply the same per-site decision rule as Task 1 Step 2.

- [ ] **Step 2: (Conditional) Add new test** if Step 1 calls for one. Test must pre-refactor pass against the still-N+1 code.

- [ ] **Step 3: Apply the refactor.** Replace `sales_invoice.py:875-895`:

```python
def validate_time_sheets_are_submitted(self):
    timesheets = self.timesheets
    detail_names = {data.timesheet_detail for data in timesheets if data.time_sheet and data.timesheet_detail}
    sheet_names = {data.time_sheet for data in timesheets if data.time_sheet}

    detail_invoice_map = {}
    if detail_names:
        detail_invoice_map = {
            r["name"]: r["sales_invoice"]
            for r in frappe.db.get_all(
                "Timesheet Detail",
                filters={"name": ("in", list(detail_names))},
                fields=["name", "sales_invoice"],
            )
        }

    sheet_status_map = {}
    if sheet_names:
        sheet_status_map = {
            r["name"]: r["status"]
            for r in frappe.db.get_all(
                "Timesheet",
                filters={"name": ("in", list(sheet_names))},
                fields=["name", "status"],
            )
        }

    for data in timesheets:
        if data.time_sheet and data.timesheet_detail:
            if sales_invoice := detail_invoice_map.get(data.timesheet_detail):
                frappe.throw(
                    _("Row {0}: Sales Invoice {1} is already created for {2}").format(
                        data.idx, frappe.bold(sales_invoice), frappe.bold(data.time_sheet)
                    )
                )

        if data.time_sheet:
            status = sheet_status_map.get(data.time_sheet)
            if status not in ["Submitted", "Payslip", "Partially Billed"]:
                frappe.throw(
                    _("Timesheet {0} cannot be invoiced in its current state").format(data.time_sheet)
                )
```

Note: the walrus assignment on the dict lookup still works (returns the truthy `sales_invoice` value or `None`). Both throws fire on the first failing row in iteration order — preserved.

- [ ] **Step 4: Run the sales_invoice test module.** Must be green.

- [ ] **Step 5: Commit.**
```bash
git add erpnext/accounts/doctype/sales_invoice/sales_invoice.py
# add test_sales_invoice.py only if Step 2 added a test
git commit -m "refactor(sales_invoice): batch Timesheet lookups in validate_time_sheets_are_submitted"
```

### Task 4: Measurement script + before/after capture + report

**Files:**
- Create: `docs/measurements/scripts/measure_n1_refactor.py`
- Create: `docs/measurements/02-sales-invoice-n1-batching.md`

- [ ] **Step 1: Create directories.**
```bash
mkdir -p docs/measurements/scripts
```

- [ ] **Step 2: Write `docs/measurements/scripts/measure_n1_refactor.py`.** Script shape:

```python
"""Re-runnable measurement: queries + wall-clock for SalesInvoice validation methods.

Usage:
    cd ~/frappe-bench
    env/bin/python ~/erpnext/docs/measurements/scripts/measure_n1_refactor.py \\
        --site test_site --output /tmp/measure_<commit>.json

The script reports per-(site, N) tuple: (queries, latency_ms_median) over 5 runs
after 1 warm-up iteration. frappe.local.cache is cleared between every iteration.
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
    frappe.local.cache = {}
    # frappe.db.get_value(..., cache=True) populates frappe.db.value_cache (the DB-instance
    # cache), NOT frappe.local.cache. Without clearing both, Site 2's warm-up populates the
    # value_cache and all 5 measured iterations are hits → 0 queries → BEFORE/AFTER inverted.
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


def build_site1_invoice(n, asset_names):
    """Build an unsaved SI with N fixed-asset items pointing at the supplied submitted Assets."""
    # Implementation detail: deferred to script-writing time.
    # Uses create_sales_invoice(do_not_save=1) + si.append("items", {...}) N-1 more times,
    # each item with is_fixed_asset=1 and asset=<one of asset_names>.
    raise NotImplementedError


def build_site2_invoice(n, so_names, dn_names):
    """Build an unsaved SI with N items each referencing a distinct submitted SO and DN."""
    raise NotImplementedError


def build_site3_invoice(n, timesheet_names, detail_names):
    """Build an unsaved SI with N timesheet entries (time_sheet + timesheet_detail set)."""
    raise NotImplementedError


def ensure_fixtures(n_max):
    """Create or look up submitted Assets, SOs, DNs, Timesheets for measurement.

    Returns a dict with lists of names per fixture type, all with at least n_max distinct entries.
    Idempotent — uses get-or-create. Fixture names are namespaced with a `_measure_n1_` prefix
    so they don't collide with test fixtures.
    """
    raise NotImplementedError


def run_all_sites(site, output_path):
    frappe.init(site=site)
    frappe.connect()
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
```

Note: the four `build_site*_invoice` and `ensure_fixtures` helpers are sketched as `NotImplementedError`. The script-writer must fill them at write time using `create_sales_invoice(do_not_save=1)` (from `test_sales_invoice.py:4870`) + `si.append("items", {...})` for items, plus minimal builders for the underlying Asset / Sales Order / Delivery Note / Timesheet / Timesheet Detail fixtures (the existing test_sales_invoice.py module has helpers for many of these — survey and reuse before inventing). Each builder should be 10-40 lines.

- [ ] **Step 3: Capture BEFORE numbers** by checking out the parent of Task 1's commit. The pre-refactor parent is `git rev-parse <Task-1-commit>^` (resolve at run time).

```bash
# Stash the new script + report draft (anything uncommitted)
git stash push -u -m "n1-measurement-pending" -- docs/measurements/

# Identify the pre-refactor commit. Task 1's commit is the first refactor commit;
# its parent is the pre-refactor state (= db7f4a669f at plan-write time, but resolve fresh).
PRE_REFACTOR=$(git rev-list --reverse HEAD ^db7f4a669f | head -1)^   # parent of first new commit
# Sanity: PRE_REFACTOR should be db7f4a669f if no other commits intervened.
git rev-parse $PRE_REFACTOR

git checkout $PRE_REFACTOR
git stash pop                                         # script reappears in working tree

cd ~/frappe-bench
env/bin/python ~/erpnext/docs/measurements/scripts/measure_n1_refactor.py \
    --site test_site --output /tmp/measure_before.json
cd ~/erpnext

git stash push -u -m "n1-measurement-pending" -- docs/measurements/
git checkout refactoring                              # return to HEAD
git stash pop                                         # script back in tree
```

If at any point `git stash pop` produces a conflict (shouldn't — `docs/measurements/` is new content), resolve by keeping the stashed version (`git checkout --theirs docs/measurements/`).

- [ ] **Step 4: Capture AFTER numbers** (tree is now at HEAD with the 3 refactors landed).
```bash
cd ~/frappe-bench
env/bin/python ~/erpnext/docs/measurements/scripts/measure_n1_refactor.py \
    --site test_site --output /tmp/measure_after.json
cd ~/erpnext
```

- [ ] **Step 5: Write `docs/measurements/02-sales-invoice-n1-batching.md`.** Sections:
  1. **Methodology** — distilled from spec §"Measurement protocol" + cache discipline note.
  2. **Per-site results table** — columns: `site | method | N | queries before | queries after | queries Δ | latency_ms before | latency_ms after | latency Δ`. Rows from `/tmp/measure_before.json` joined with `/tmp/measure_after.json` on `(site, method, n)`.
  3. **Inline script source** — paste the script verbatim (or link to it relatively and quote key sections).
  4. **Notes on noise / variance** — if any run's wall-clock varied >20% across the 5 captures, flag it; note local-MariaDB latency profile.
  5. **References** — link to spec, plan, and the lesson (Task 5).

- [ ] **Step 6: Commit.**
```bash
git add docs/measurements/scripts/measure_n1_refactor.py docs/measurements/02-sales-invoice-n1-batching.md
git commit -m "docs: measure SalesInvoice N+1 batching before/after"
```

### Task 5: Lesson

**Files:**
- Create: `docs/lessons/02-intermediate-sales-invoice-n1s.md`

- [ ] **Step 1: Write the lesson** mirroring `docs/lessons/01-warm-up-update-payment-schedule.md`'s shape:
  - Title: "Lesson 2: SalesInvoice N+1 Batching (Intermediate)"
  - **Commits / refs** section: list the 3 refactor commit SHAs (Tasks 1-3), the measurement commit (Task 4), source spec, plan, and this commit.
  - **What we found** — restate the 5 N+1s across 3 methods, with the spec's framing.
  - **How we identified it** — arch review's Finding 3.2 + the verification re-scope (load-modify-save vs. read-N+1 dead-end).
  - **What we did** — for each site: before/after code snippets (small), plus the cache-discipline insight for Site 2.
  - **What we measured** — cite the actual numbers from `docs/measurements/02-sales-invoice-n1-batching.md` (queries Δ and latency Δ per site at N ∈ {1, 5, 20}). Compare against the spec's swag predictions; explain any gap.
  - **What we learned** — pedagogical takeaways: (a) arch-review's mechanical "DB-in-loop" detector flags both real N+1s AND load-modify-save patterns — manual verification of loop body shape is required; (b) `cache=True` interacts non-obviously with warm-up iterations in measurement protocols; (c) inline 2-pass scales to 5 N+1s across 3 methods without helper extraction when loop bodies are small and single-concern; (d) measured numbers may diverge from swag — explain what the divergence taught us.

- [ ] **Step 2: Commit.**
```bash
git add docs/lessons/02-intermediate-sales-invoice-n1s.md
git commit -m "docs: add intermediate lesson — SalesInvoice N+1 batching"
```

## Tasks NOT in this plan

(Inherited verbatim from spec's "Out of scope" section)

- The other 16 N+1 sites in `sales_invoice.py` (Finding 3.2 lists 19 total; this exercise addresses 3).
- The two original arch-review sites (`unlink_sales_invoice_from_timesheets` at L728, `cancel_pos_invoice_credit_note_generated_during_sales_invoice_mode` at L735) — load-modify-save patterns where the standard batching pattern doesn't apply cleanly. A separate exercise could rewrite them via bulk SQL UPDATEs (bypassing hooks), but that has different correctness implications and isn't on this exercise's path.
- Any structural refactor of the methods containing these sites (e.g., the 98-line `set_pos_fields` from Finding 2.8).
- Helper extraction (warm-up's pattern). Each loop body is small enough that 2-pass inline is the minimal change that solves the problem.
- Replacing `frappe.db.get_value(... cache=True)` with `frappe.db.get_cached_value` (Frappe's per-request cache helper). The batched query supersedes the need for caching at these sites.

## Known issues inherited from spec

(Inherited verbatim from spec's "Known issues / accepted as out of scope" section)

- **Test coverage audit decision is per-site at implementation time, not pre-decided here.** The spec doesn't pre-commit to "N new tests will be added"; the count is 0-3, decided during implementation by grepping `test_sales_invoice.py` for each method's execution path.
- **`cache=True` semantic drift on site 2 is theoretically observable** in adversarial mid-request mutation scenarios (another code path mutates SO docstatus between the batched fetch and the loop). In practice, docstatus is set when documents are submitted/cancelled and is stable across an SI's validation. Flagged but not blocking.
- **Measurement at small N may be dominated by fixture-construction and framework overhead.** The script reports the target-method's own wall-clock (measured tightly), but if the per-method delta is in the single-millisecond range, MariaDB's per-query variance can swamp it. The report's "noise / variance" section flags this if observed.
- **Measurement uses local-network MariaDB on a dev machine.** Production deployments on WAN-attached DBs see larger per-query latency, so the latency savings reported here are a lower bound for that deployment shape.
