# Architecture Review: erpnext (Round 1)

**Repo:** /home/yv01p/erpnext
**Brief (verbatim):** "Arch review with a focus on classes that can benefit from being refactored. Feel free to group them as you see convenient (eg: number of lines, performance, cyclomatic complexity, etc)"

## 1. Literal-wrongness findings

The brief asks for refactor-candidate classes, grouped by metric. Without surfacing the classes below, the brief's outcome is unaddressed. Findings are grouped by the metric that flags the candidate; within each group, items are ordered by severity of the metric. Evidence is from raw file inspection (line counts, method counts, branch counts, and locations of database calls inside loops).

### Finding 1 — God classes (single classes >2000 lines OR >80 methods)

These are single Python classes that have outgrown a single-file/single-class boundary. Each one mixes validation, persistence, GL posting, status transitions, and integration glue inside the same object. Method counts are the actual `def` count within the class block (not the file).

| # | Class | Location | Class lines | Methods | Branches in file |
|---|---|---|---|---|---|
| 1.1 | `AccountsController` | `erpnext/controllers/accounts_controller.py:106` | ~4388 | 134 | 624 |
| 1.2 | `StockEntry` | `erpnext/stock/doctype/stock_entry/stock_entry.py:86` | ~4331 | 123 | 596 |
| 1.3 | `PaymentEntry` | `erpnext/accounts/doctype/payment_entry/payment_entry.py:63` | ~3508 | 80 | 433 |
| 1.4 | `SerialandBatchBundle` | `erpnext/stock/doctype/serial_and_batch_bundle/serial_and_batch_bundle.py:55` | ~3459 | 67 | 510 |
| 1.5 | `SalesInvoice` | `erpnext/accounts/doctype/sales_invoice/sales_invoice.py:58` | ~3118 | 108 | 361 |
| 1.6 | `WorkOrder` | `erpnext/manufacturing/doctype/work_order/work_order.py:69` | ~2821 | 86 | 348 |
| 1.7 | `ProductionPlan` | `erpnext/manufacturing/doctype/production_plan/production_plan.py:40` | ~2224 | 51 | 247 |
| 1.8 | `SalesOrder` | `erpnext/selling/doctype/sales_order/sales_order.py:54` | ~2085 | 75 | 204 |
| 1.9 | `BOM` | `erpnext/manufacturing/doctype/bom/bom.py:104` | ~1916 | 73 | 253 |
| 1.10 | `SubcontractingController` | `erpnext/controllers/subcontracting_controller.py:26` | ~1560 | 62 | 243 |

**Frontend mirrors of the same pattern** (Doctype controller JS files >1200 lines):

| # | File | Lines |
|---|---|---|
| 1.11 | `erpnext/public/js/controllers/transaction.js` | 3342 |
| 1.12 | `erpnext/accounts/doctype/payment_entry/payment_entry.js` | 1888 |
| 1.13 | `erpnext/selling/doctype/sales_order/sales_order.js` | 1869 |
| 1.14 | `erpnext/stock/doctype/stock_entry/stock_entry.js` | 1643 |
| 1.15 | `erpnext/public/js/utils.js` | 1407 |
| 1.16 | `erpnext/manufacturing/doctype/work_order/work_order.js` | 1274 |
| 1.17 | `erpnext/accounts/doctype/sales_invoice/sales_invoice.js` | 1230 |
| 1.18 | `erpnext/stock/doctype/item/item.js` | 1208 |
| 1.19 | `erpnext/public/js/controllers/taxes_and_totals.js` | 1176 |
| 1.20 | `erpnext/manufacturing/doctype/bom/bom.js` | 1133 |

**Proposed fix.** Extract by responsibility into co-located submodules behind the existing DocType class, leaving the class as a thin orchestrator. Concretely:
- For `AccountsController` / `StockEntry` / `PaymentEntry` / `SalesInvoice`: split into per-concern helper modules (`validation.py`, `gl_posting.py`, `status.py`, `references.py`, etc.) under the same DocType directory; have the DocType class delegate. Avoids fighting the Frappe one-class-per-DocType convention while breaking the file open.
- For `SerialandBatchBundle`: this file declares five classes (`SerialandBatchBundle` plus four helpers); the helpers can move to dedicated files (`serial_no_writer.py`, `batch_no_writer.py`) without callsite churn since they're already separable.
- For the JS controllers: the same split applies — `transaction.js` is the base of every transaction form; today it owns event handling, calculation, validation, and server round-trips in one closure.

### Finding 2 — Methods >100 lines (cyclomatic complexity proxy)

Long methods almost always indicate a missed extraction; in this codebase they additionally tend to mix two or three orthogonal concerns (validation + side-effects + GL posting) inside a single transaction step, which is what makes them the riskiest places to change.

| # | Method | Location | Lines |
|---|---|---|---|
| 2.1 | `process_sle` | `erpnext/stock/stock_ledger.py:838` | 169 |
| 2.2 | `validate_subcontract_order` | `erpnext/stock/doctype/stock_entry/stock_entry.py:1534` | 165 |
| 2.3 | `set_missing_item_details` | `erpnext/controllers/accounts_controller.py:997` | 141 |
| 2.4 | `add_party_gl_entries` | `erpnext/accounts/doctype/payment_entry/payment_entry.py:1316` | 126 |
| 2.5 | `update_payment_schedule` | `erpnext/accounts/doctype/payment_entry/payment_entry.py:782` | 114 |
| 2.6 | `validate_subcontracting_inward_order` | `erpnext/manufacturing/doctype/work_order/work_order.py:295` | 105 |
| 2.7 | `get_gl_dict` | `erpnext/controllers/accounts_controller.py:1299` | 101 |
| 2.8 | `set_pos_fields` | `erpnext/accounts/doctype/sales_invoice/sales_invoice.py:897` | 98 |
| 2.9 | `set_incoming_rate_for_inward_transaction` | `erpnext/stock/doctype/serial_and_batch_bundle/serial_and_batch_bundle.py:730` | 97 |
| 2.10 | `validate` (AccountsController) | `erpnext/controllers/accounts_controller.py:220` | 96 |
| 2.11 | `validate` (SalesInvoice) | `erpnext/accounts/doctype/sales_invoice/sales_invoice.py:299` | 93 |
| 2.12 | `check_future_entries_exists` | `erpnext/stock/doctype/serial_and_batch_bundle/serial_and_batch_bundle.py:896` | 92 |
| 2.13 | `validate_allocated_amount_with_latest_data` | `erpnext/accounts/doctype/payment_entry/payment_entry.py:417` | 85 |
| 2.14 | `on_submit` (SalesInvoice) | `erpnext/accounts/doctype/sales_invoice/sales_invoice.py:449` | 82 |
| 2.15 | `validate_warehouse` | `erpnext/stock/doctype/stock_entry/stock_entry.py:805` | 80 |

`process_sle` and the two `validate` orchestrators are the highest-leverage targets: each is called on every relevant transaction posting and any incorrect edit propagates everywhere. They're also the methods most heavily branched on document type / posting state, which makes them prime candidates for a strategy-pattern split or a small state-machine.

**Proposed fix.** Extract numbered phases into named functions with a single responsibility each (e.g. `process_sle` → `_load_previous_sle`, `_compute_new_balance`, `_handle_serial_batch`, `_persist`). Don't change behaviour or signatures of the public methods; only break the bodies. Each extracted helper should be independently testable.

### Finding 3 — Performance hot spots: database calls inside loops (N+1 risk)

These are call sites where `for ... in <row collection>` is followed (in the loop body, before the next iteration) by a Frappe DB call (`frappe.db.get_value`, `frappe.db.sql`, `frappe.get_doc`, `frappe.get_cached_value`, etc.). For documents with hundreds of rows (stock entries, BOMs, production plans), this is `O(rows)` round-trips per save.

Aggregated counts (per file, distinct loops with a DB call in the body):

| # | File | DB-in-loop candidates |
|---|---|---|
| 3.1 | `erpnext/stock/doctype/stock_entry/stock_entry.py` | 21 |
| 3.2 | `erpnext/accounts/doctype/sales_invoice/sales_invoice.py` | 19 |
| 3.3 | `erpnext/controllers/accounts_controller.py` | 16 |
| 3.4 | `erpnext/accounts/doctype/payment_entry/payment_entry.py` | 13 |
| 3.5 | `erpnext/manufacturing/doctype/production_plan/production_plan.py` | 11 |
| 3.6 | `erpnext/controllers/subcontracting_controller.py` | 11 |
| 3.7 | `erpnext/accounts/utils.py` | 9 |
| 3.8 | `erpnext/manufacturing/doctype/bom/bom.py` | 8 |
| 3.9 | `erpnext/manufacturing/doctype/serial_and_batch_bundle/...` | 7 |
| 3.10 | `erpnext/manufacturing/doctype/work_order/work_order.py` | 7 |

Sample evidence (representative spots — full set is recoverable with the analysis pattern in step 5):
- `erpnext/stock/doctype/stock_entry/stock_entry.py:601` — `for ... :` → L603 `frappe.db.get_value(...)` (lookup per row).
- `erpnext/stock/doctype/stock_entry/stock_entry.py:638` — `for ... :` → L639 `frappe.db.sql(...)` (raw SQL per row).
- `erpnext/stock/doctype/stock_entry/stock_entry.py:943` — `for ... :` → L951 `frappe.db.get_value("Job Card", {"operation_id": d.name}, "name")`.
- `erpnext/accounts/doctype/sales_invoice/sales_invoice.py:408` — `for ... :` → L412 `frappe.db.get_value("Asset", d.asset, "status")` per asset row.
- `erpnext/accounts/doctype/sales_invoice/sales_invoice.py:729` — `for ... :` → L730 `frappe.get_doc("Timesheet", ...)` per timesheet row.
- `erpnext/accounts/doctype/sales_invoice/sales_invoice.py:740` — `for ... :` → L741 `frappe.get_doc("POS Invoice", pos_invoice)` per consolidated invoice.
- `erpnext/manufacturing/doctype/production_plan/production_plan.py:669` and `:675` — `for ... :` → `frappe.get_doc("Bin", bin_name, for_update=True)` **with row-level row lock per item**; lock-fanout grows linearly with item count.
- `erpnext/manufacturing/doctype/production_plan/production_plan.py:727` and `:807` — `for ... :` → `frappe.get_value("BOM", ..., "default_source_warehouse")` per row.
- `erpnext/controllers/subcontracting_controller.py:145` — `for ... :` → L146 `frappe.get_value(... is_stock_item, is_sub_contracted_item ...)` per supplied item.
- `erpnext/controllers/subcontracting_controller.py:1167` — `for ... :` → L1169 `frappe.get_doc("Subcontracting Order", sco)` (full doc load per SCO).

**Proposed fix.** Each of these patterns has the same shape: collect the set of keys before the loop, issue one batched query (`frappe.db.get_values` with a name list, or `frappe.qb` with `.isin()`), build a `{key: row}` dict, and dereference inside the loop. Cached variants (`get_cached_value`) help only when the same key is hit repeatedly within a request; they don't help when keys are unique per row. The for-row `get_doc(..., for_update=True)` in `production_plan.py` is the worst offender — replace with a single `SELECT ... FOR UPDATE` over the bin set.

### Finding 4 — Cross-concern files masquerading as utility modules (~2000+ lines, no class boundary)

These aren't classes, but they're load-bearing modules that the brief's grouping criteria (size, complexity) flag as refactor candidates with the same shape as the god classes:

| # | Module | Lines |
|---|---|---|
| 4.1 | `erpnext/accounts/utils.py` | 2712 |
| 4.2 | `erpnext/stock/stock_ledger.py` | 2472 |
| 4.3 | `erpnext/stock/get_item_details.py` | 1752 |

These are the canonical "everyone imports from here" modules; their size means callers can't tell which subsystem they're coupling to. `stock_ledger.py` additionally contains the 169-line `process_sle` flagged in 2.1.

**Proposed fix.** Split into per-concern submodules under a package (e.g. `erpnext/accounts/utils/{gl.py, payment.py, dimensions.py, ...}`) and re-export from `__init__.py` to preserve import paths. No callsite changes; just makes the surface area readable and the test boundaries explicit.

## 2. Forced decisions

No forced decisions found.

The brief asks for identification of refactor candidates, not for a refactor strategy. The user can take the candidates above and decide independently which to address, in what order, and with what technique. No codebase constraint forces the user to pick anything before proceeding with the brief's stated outcome.

## 3. Recommendation

⚠️ **Literal-wrongness findings present.** §1 surfaces refactor candidates grouped by (a) god-class size/method-count, (b) method length / cyclomatic-complexity proxy, (c) N+1 / DB-in-loop performance hot spots, and (d) cross-concern utility modules. The user can prioritise from this list; no decisions are blocked.
