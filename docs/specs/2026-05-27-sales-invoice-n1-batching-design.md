# SalesInvoice N+1 Batching Refactor — Design Spec

## Goal

Eliminate 5 read-N+1 patterns across 3 methods in `erpnext/accounts/doctype/sales_invoice/sales_invoice.py` (representative subset of Finding 3.2 in `docs/reviews/2026-05-27-erpnext-architecture-review-1.md`). Apply the standard collect-IDs-then-batch-query pattern in place at each site. No new helper methods. Behavior preservation is the hard constraint: same writes (none here — these are validation reads), same exceptions in the same order, same iteration shape.

**Additionally:** capture before/after query counts and wall-clock latency for each site via a re-runnable measurement script, with results recorded in a measurement report. This replaces the warm-up's "theoretical, not measured" caveat — now that Bench is bootstrapped, we measure.

This is the **intermediate** refactor in a 3-part didactical series. The warm-up (`PaymentEntry.update_payment_schedule`, landed at `601f3f1a1b`) tackled 2 N+1s in 1 method with method extraction. This exercise scales up the N+1 surface (5 vs 2), without method extraction (loops are small enough that inline 2-pass is the right shape), and adds empirical measurement.

## Architecture

Three methods on the `SalesInvoice` DocType class are modified in place. Each method's `for` loop is replaced with the same shape:

1. **Pre-pass:** collect identifiers from `self` into a list (or sets, per site).
2. **One batched query** per concern: `frappe.db.get_all(doctype, filters={"name": ("in", list)}, fields=[...])` (existing codebase pattern, used by the warm-up at `payment_entry.py:824-832` and elsewhere in `purchase_invoice.py:1924`).
3. **Existing loop body** dereferences from a `{name: row}` dict instead of calling `frappe.db.get_value` per iteration.

No new methods on the class. No new imports.

## Tech stack

- Python 3.10+ (project targets 3.14, ruff `target_version = "py310"`).
- Frappe v16 framework: `frappe.db.get_all`, `frappe.throw`, `_` (translation helper).
- MariaDB via Frappe Bench (`~/frappe-bench/`).

## File structure

- **Modify:** `erpnext/accounts/doctype/sales_invoice/sales_invoice.py` — three methods (`validate_fixed_asset` at L404, `check_prev_docstatus` at L1391, `validate_time_sheets_are_submitted` at L875).
- **Modify (conditionally):** `erpnext/accounts/doctype/sales_invoice/test_sales_invoice.py` — per-site, 0-1 new test methods depending on the coverage audit performed during implementation.
- **Create:** `docs/measurements/scripts/measure_n1_refactor.py` — re-runnable measurement script (query counter + perf_counter wrappers; builds N-row Sales Invoices via existing test fixture helpers; invokes the 3 target methods).
- **Create:** `docs/measurements/02-sales-invoice-n1-batching.md` — measurement report (methodology, before/after numbers per site at small + large N, observed query and latency deltas).
- **Create:** `docs/lessons/02-intermediate-sales-invoice-n1s.md` — pedagogical write-up that cites the measured numbers (not the swag).

## The 3 sites

### Site 1: `validate_fixed_asset` (L404-429) — Asset.status N+1 at L412

**Current shape:**

```python
def validate_fixed_asset(self):
    if self.doctype != "Sales Invoice":
        return
    for d in self.get("items"):
        if d.is_fixed_asset:
            if d.asset:
                if not self.is_return:
                    asset_status = frappe.db.get_value("Asset", d.asset, "status")
                    if self.update_stock:
                        frappe.throw(_("'Update Stock' cannot be checked for fixed asset sale"))
                    elif asset_status in ("Scrapped", "Cancelled", "Capitalized"):
                        frappe.throw(...)
                    elif asset_status == "Sold" and not self.is_return:
                        frappe.throw(...)
                elif not self.return_against:
                    frappe.throw(...)
            else:
                frappe.throw(...)
```

**Per-row DB call:** `frappe.db.get_value("Asset", d.asset, "status")` — one query per fixed-asset item that has an `asset` link, when not a return.

**Batched replacement:** Before the loop, collect the set of `d.asset` names where `d.is_fixed_asset and d.asset and not self.is_return`. Issue one `frappe.db.get_all("Asset", filters={"name": ("in", list(asset_names))}, fields=["name", "status"])`, build `{name: status}`. Replace L412 with a dict lookup.

**Skip the batched fetch entirely** when `self.is_return` is True (no iteration in the loop reads asset_status in that branch).

**Behavior preservation:**
- The `frappe.throw` calls inside the if-else chain are unchanged — they still fire on the same `asset_status` values.
- Items without `is_fixed_asset` or without `asset` are unaffected (the iteration still visits them and falls through to the existing branches).
- `self.update_stock` check (L413) and the `elif not self.return_against` branch (L424) are unchanged.

### Site 2: `check_prev_docstatus` (L1391-1403) — Sales Order + Delivery Note docstatus N+1s (2 total)

**Current shape:**

```python
def check_prev_docstatus(self):
    for d in self.get("items"):
        if (
            d.sales_order
            and frappe.db.get_value("Sales Order", d.sales_order, "docstatus", cache=True) != 1
        ):
            frappe.throw(_("Sales Order {0} is not submitted").format(d.sales_order))
        if (
            d.delivery_note
            and frappe.db.get_value("Delivery Note", d.delivery_note, "docstatus", cache=True) != 1
        ):
            throw(_("Delivery Note {0} is not submitted").format(d.delivery_note))
```

**Per-row DB calls:** Two — one for Sales Order docstatus, one for Delivery Note docstatus, both with `cache=True`.

**Batched replacement:** Before the loop, collect `{d.sales_order for d in items if d.sales_order}` and `{d.delivery_note for d in items if d.delivery_note}`. Issue two `frappe.db.get_all` queries (or skip empty sets). Replace the per-row `get_value` calls with dict lookups.

**Note on `cache=True`:** The original uses Frappe's request-scoped cache. The batched version queries fresh once per request and dereferences from an in-memory dict. Both produce the same observed value for the duration of a request because the per-row code already caches after first miss. The only conceptual difference: if some other code path mutates the Sales Order's docstatus between our batched fetch and the loop's iteration, the batched version sees the pre-mutation value (just like the cached version would after first miss). Docstatus rarely mutates mid-request, so this is not a behavior concern.

**Behavior preservation:**
- `frappe.throw` fires on the first item whose linked SO or DN is not submitted — preserved (we still iterate items in order and throw on first failure).
- Error messages and substitutions unchanged.

### Site 3: `validate_time_sheets_are_submitted` (L875-895) — Timesheet Detail + Timesheet status N+1s (2 total)

**Current shape:**

```python
def validate_time_sheets_are_submitted(self):
    for data in self.timesheets:
        if data.time_sheet and data.timesheet_detail:
            if sales_invoice := frappe.db.get_value(
                "Timesheet Detail", data.timesheet_detail, "sales_invoice"
            ):
                frappe.throw(
                    _("Row {0}: Sales Invoice {1} is already created for {2}").format(
                        data.idx, frappe.bold(sales_invoice), frappe.bold(data.time_sheet)
                    )
                )
        if data.time_sheet:
            status = frappe.db.get_value("Timesheet", data.time_sheet, "status")
            if status not in ["Submitted", "Payslip", "Partially Billed"]:
                frappe.throw(
                    _("Timesheet {0} cannot be invoiced in its current state").format(data.time_sheet)
                )
```

**Per-row DB calls:** Two — Timesheet Detail's `sales_invoice` field, and Timesheet's `status` field.

**Batched replacement:** Before the loop, collect `{data.timesheet_detail for data in self.timesheets if data.time_sheet and data.timesheet_detail}` and `{data.time_sheet for data in self.timesheets if data.time_sheet}`. Two `frappe.db.get_all` queries. Replace the per-row `get_value` calls with dict lookups.

**Behavior preservation:**
- Both throws fire on the first failing row, in iteration order — preserved.
- The Timesheet Detail throw fires only when the loaded `sales_invoice` field is truthy — preserved (dict lookup returns the same value, the walrus assignment still works on the lookup result).
- Error messages and substitutions unchanged.

## Verified assumptions

| # | Assumption | Status | Evidence |
|---|---|---|---|
| A1 | `sales_invoice.py:408` is `for d in self.get("items"):` and `:412` is `frappe.db.get_value("Asset", d.asset, "status")` | ✅ | Direct read of L400-429 |
| A2 | Loop iterates `self.get("items")` (not `self.assets` as initially assumed); items with `is_fixed_asset` carry the `asset` link | ✅ | Direct read; correction folded into the design |
| A3 | The L408-412 loop body uses only Asset's `.status` field | ✅ | Direct read of L408-423 — only `asset_status` is read; throws use only `d.idx`, `d.asset`, `asset_status` |
| A4-A9 | Sites 2 and 3 cited in arch review (L729-730 Timesheet, L740-741 POS Invoice) | ✅ verified but ❌ disqualified — these are load-modify-save loops, not read-N+1s | Direct read; surfaced as forced decision in brainstorming; user re-scoped to two fresh sites |
| A10 | `frappe.db.get_all(doctype, filters={"name": ("in", list)}, fields=[...])` works as expected | ✅ | Used by warm-up at `payment_entry.py:828-832`, lands green in tests |
| A11 | `frappe.db.get_all` and `frappe.db.get_value` apply the same permission scope for internal system calls | ✅ | Same as warm-up's verification; both bypass User Permissions when called outside a request context |
| A12 | No Frappe hook on `frappe.get_doc("Timesheet"/"POS Invoice")` with observable side effects | N/A | Sites 2 and 3 dropped — no longer use `get_doc` |
| A13 | Per-iteration DB calls are pure reads (no row-level locks, no `for_update`, no `set_value` mid-loop) | ✅ | All three sites: only `get_value` (read); throws don't mutate |
| A14 | Loop bodies are small enough that inline 2-pass is the right shape (no extraction needed) | ✅ | Site 1: 20 lines including nested if-chain; Site 2: 12 lines; Site 3: 20 lines — all well within "inline" range |
| A15 | `test_sales_invoice.py` exists | ✅ | 155740 bytes |
| A16 | Test class is `TestSalesInvoice(ERPNextTestSuite)` — matches warm-up's pattern | ✅ | `test_sales_invoice.py:53` |
| A17 | `bench --site test_site run-tests --module erpnext.accounts.doctype.sales_invoice.test_sales_invoice --lightmode` is correct | ✅ | Same shape as warm-up's Step 4 invocation |
| A18 | Bench env that ran payment_entry tests will also run sales_invoice tests | ✅ assumed; verified at implementation time | Same site, same apps, same fixtures bootstrap |
| A19 | `test_sales_invoice.py` is grep-able for per-site coverage audits | ✅ | Plain Python module, easy to grep |
| A20 | The 3 modified methods aren't called from contexts that depend on per-iteration DB timing | ✅ | All 3 are validation methods called from `validate()` / `on_submit()` — no API contracts on timing |
| A21 | No existing tests mock `frappe.db.get_value("Asset"/"Sales Order"/"Delivery Note"/"Timesheet"/"Timesheet Detail")` | ✅ | `grep -nE 'mock\|patch\|MagicMock' test_sales_invoice.py` returns 1 unrelated hit |
| A22 | Exception messages identical | ✅ | The same `frappe.throw(_(...))` calls fire with the same template strings; only the source of the value changes (dict lookup vs. per-row query) |
| A23 | Arch review's cited line numbers reflect current state of `sales_invoice.py` on the `refactoring` branch | ✅ | `git log d25d0e8d05..HEAD -- erpnext/accounts/doctype/sales_invoice/sales_invoice.py` returns no commits |
| A24 | The 3 final sites are in different methods (independently committable) | ✅ | `validate_fixed_asset` (L404), `check_prev_docstatus` (L1391), `validate_time_sheets_are_submitted` (L875) |
| AM1 | `frappe.db.sql` can be monkey-patched at runtime to count calls | ✅ trivial | `frappe.db` is a `Database` instance; `frappe.db.sql` is a bound method, replaceable in place |
| AM2 | `time.perf_counter()` has sufficient resolution for sub-millisecond measurements | ✅ trivial | Stdlib; nanosecond-resolution on Linux |
| AM3 | Building Sales Invoices with N=20 items via existing fixture helpers + `si.append("items", {...})` is tractable | ✅ assumed; verified at plan-writing time when the script is drafted | Standard Frappe pattern; `create_sales_invoice(do_not_save=1)` returns a doc you can mutate |
| AM4 | Re-running the script after `git checkout <pre-refactor-sha>` gives valid "before" numbers without environment poisoning between runs | ✅ assumed; verified at plan-writing time | Bench's `bench reinstall` baseline + fresh fixture creation per run; details in the plan |
| AM5 | The 3 target methods can be invoked directly (`si.validate_fixed_asset()` etc.) without a full submit, given a properly populated `si` doc | ✅ | All 3 are public methods on `SalesInvoice` taking only `self` |

### Forced decision surfaced during verification

The arch review's "3 representative sites" included two load-modify-save loops (Timesheet unlink at L729, POS Invoice cancel at L740) where the standard read-batching pattern doesn't apply: the `frappe.get_doc(...)` loads a full document specifically so a mutating method can be called on the instance, then `db_update_all()` / `cancel()` persists per-row. The arch review's mechanical "DB-in-loop" discovery doesn't distinguish read-N+1 from load-modify-save. The user re-scoped to two fresh sites (`check_prev_docstatus`, `validate_time_sheets_are_submitted`) that ARE genuine read-N+1s, after seeing the verification finding.

## Approach (per site, repeated × 3)

1. **Audit test coverage.** Grep `test_sales_invoice.py` for any test method whose execution path passes through the target method (most likely indirect — submit/validate flows). Decide:
   - If existing tests already exercise the loop with at least 2 distinct rows AND assert post-loop state — no new test.
   - If existing tests only cover happy-path / single-row — add one targeted test that exercises the loop with multiple rows.
   - If no test reaches the method — add a targeted test (the "A6 gap" analog from the warm-up).
2. **Refactor in place.** Pre-pass collects identifiers; one (or two, for sites 2 and 3) `frappe.db.get_all` calls; existing loop body dereferences from `{name: value}` dict instead of per-row `get_value`.
3. **Run bench:** `cd ~/frappe-bench && ~/.local/bin/bench --site test_site run-tests --module erpnext.accounts.doctype.sales_invoice.test_sales_invoice --lightmode`. All tests must pass.
4. **Commit:** `refactor(sales_invoice): batch <noun> lookup in <method-name>`. Folds in the new test for that site, if any.

After all 3 refactor commits land, a 4th commit adds the measurement script + report (`docs/measurements/scripts/measure_n1_refactor.py` and `docs/measurements/02-sales-invoice-n1-batching.md`), and a 5th commit creates the lesson (`docs/lessons/02-intermediate-sales-invoice-n1s.md`).

## Measurement protocol

Two dimensions per site:

1. **Query count** — total `frappe.db.sql` invocations during a single call to the target method. Captured by monkey-patching `frappe.db.sql` for the duration of the measured call (counter increments per invocation; original implementation called through).
2. **Wall-clock latency** — `time.perf_counter()` around the call; reported as the median of 5 runs after 1 warm-up iteration (to amortize first-call cache misses).

**Cache discipline.** Site 2's original code uses `frappe.db.get_value(..., cache=True)`, which populates Frappe's per-request cache on first miss. The warm-up iteration would otherwise warm this cache, making subsequent measured iterations show 0 queries (cache hits) and inverting the BEFORE vs. AFTER comparison. The script invalidates `frappe.local.cache` between every measured iteration (and between warm-up and the first measured run). This makes the BEFORE measurement reflect a cold request — the production-relevant case for the FIRST validate of a fresh Sales Invoice. The original's `cache=True` benefit on REPEATED invocations within the same request is real but not measured here; the report's "Notes on noise / variance" section acknowledges this scope choice. Sites 1 and 3 do not use `cache=True` and are unaffected; the same cache-clear runs for them too for uniformity but is a no-op.

**Workload per site:**

| Site | Method | Workload variants |
|---|---|---|
| 1 | `validate_fixed_asset` | A Sales Invoice with N fixed-asset items (`is_fixed_asset=1`, `asset` set), for N ∈ {1, 5, 20}. |
| 2 | `check_prev_docstatus` | A Sales Invoice with N items each referencing a distinct submitted Sales Order AND a distinct submitted Delivery Note, for N ∈ {1, 5, 20}. |
| 3 | `validate_time_sheets_are_submitted` | A Sales Invoice with N timesheet entries (`time_sheet`, `timesheet_detail` set, all referencing distinct submitted Timesheets), for N ∈ {1, 5, 20}. |

**Fixtures:** The script uses existing helpers from `test_sales_invoice.py` and `erpnext/tests/utils.py` (e.g., `create_sales_invoice`, `BootStrapTestData` artifacts) to construct fixtures. If a site's fixture is non-trivial to build via existing helpers (notably site 1's fixed assets and site 3's timesheets), the script's fixture builders are themselves small (10-30 lines each) and documented inline.

**Before/after capture:** The same script is run twice — once at the pre-refactor commit (the commit that lands this spec — i.e., the immediate parent of the first refactor commit) and once at the post-refactor HEAD (after all 3 refactor commits land). Both result sets are recorded in the same report alongside the methodology and the script source. The implementation plan specifies the exact git checkout/restore dance.

**Report contents:**
1. Methodology (this section, distilled).
2. Per-site results table: `(site, N) → (queries before, queries after, latency_ms before, latency_ms after, queries delta, latency delta)`.
3. Inline script source (for reproducibility).
4. Notes on noise / variance (e.g., did wall-clock vary >20% across the 5 runs? was there cache warming impact?).
5. Pointer back to this spec + the implementation plan.

## Behavior preservation invariants (apply per site)

1. **Same writes:** none of the 3 sites write to the DB; this is trivially preserved.
2. **Same exceptions:** every `frappe.throw` call is preserved verbatim with the same arguments. The dict-lookup version raises the same exception under the same conditions as the per-row-fetch version.
3. **Iteration order unchanged:** the outer `for` loop iterates the same collection (`self.get("items")` or `self.timesheets`) in the same order. The batched fetch produces a dict, used as a lookup table; it does not reorder iteration.
4. **First-failure semantics preserved:** all 3 methods throw on the first failing row. The batched fetch happens once before iteration, but throws still fire from inside the existing loop in iteration order.
5. **Same row set:** `frappe.db.get_all(doctype, filters={"name": ("in", names)}, fields=[...])` returns the same rows as N×`frappe.db.get_value(doctype, name, field)` for system-code callers (both bypass User Permissions).
6. **`cache=True` parity:** Site 2 uses `cache=True` on the per-row `get_value`. The batched version skips the cache and queries fresh once. Within a single request, the observed values are identical (cache hits return whatever the first miss wrote, which equals the batched version's fresh read).

## Performance expectation (pre-measurement swag)

These are the predicted numbers, recorded here for falsification by the actual measurement in commit 4. If the measurement contradicts a row, the lesson explains the gap.

| Site | Queries before (predicted, per N) | Queries after (predicted) |
|------|-----------------------------------|---------------------------|
| 1 (Asset) | 1 per fixed-asset item where `not self.is_return` | 0 (if no qualifying rows) or 1 (single batched fetch) |
| 2 (SO+DN docstatus) | up to 2 per item (cache softens repeated SOs/DNs) | 0-2 (one batched fetch per side that has rows) |
| 3 (Timesheet) | up to 2 per timesheet entry | 0-2 (one batched fetch per side that has rows) |

Wall-clock prediction: at ~1-5ms per query over local-network MariaDB, the per-method latency reduction at N=20 should be 20-200ms.

Real wins are at scale: large invoices (manufacturing line items, hourly-billing timesheet invoices), batch imports, busy installations. Small-invoice impact may be invisible (lost in framework overhead) — the measurement will tell us where exactly the crossover sits.

## Out of scope

- The other 16 N+1 sites in `sales_invoice.py` (Finding 3.2 lists 19 total; this exercise addresses 3).
- The two original arch-review sites (`unlink_sales_invoice_from_timesheets` at L728, `cancel_pos_invoice_credit_note_generated_during_sales_invoice_mode` at L735) — load-modify-save patterns where the standard batching pattern doesn't apply cleanly. A separate exercise could rewrite them via bulk SQL UPDATEs (bypassing hooks), but that has different correctness implications and isn't on this exercise's path.
- Any structural refactor of the methods containing these sites (e.g., the 98-line `set_pos_fields` from Finding 2.8).
- Helper extraction (warm-up's pattern). Each loop body is small enough that 2-pass inline is the minimal change that solves the problem.
- Replacing `frappe.db.get_value(... cache=True)` with `frappe.db.get_cached_value` (Frappe's per-request cache helper). The batched query supersedes the need for caching at these sites.

## Known issues / accepted as out of scope

- **Test coverage audit decision is per-site at implementation time, not pre-decided here.** The spec doesn't pre-commit to "N new tests will be added"; the count is 0-3, decided during implementation by grepping `test_sales_invoice.py` for each method's execution path.
- **`cache=True` semantic drift on site 2 is theoretically observable** in adversarial mid-request mutation scenarios (another code path mutates SO docstatus between the batched fetch and the loop). In practice, docstatus is set when documents are submitted/cancelled and is stable across an SI's validation. Flagged but not blocking.
- **Measurement at small N may be dominated by fixture-construction and framework overhead.** The script reports the target-method's own wall-clock (measured tightly), but if the per-method delta is in the single-millisecond range, MariaDB's per-query variance can swamp it. The report's "noise / variance" section flags this if observed.
- **Measurement uses local-network MariaDB on a dev machine.** Production deployments on WAN-attached DBs see larger per-query latency, so the latency savings reported here are a lower bound for that deployment shape.
