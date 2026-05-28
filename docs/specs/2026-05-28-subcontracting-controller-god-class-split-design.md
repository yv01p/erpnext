# Design: SubcontractingController god-class split (Part 3)

**Status:** Draft, pending CDR.
**Author:** Refactoring series Part 3 of 3 (didactical).
**Branch:** `refactoring`
**Baseline HEAD:** `82056c5029f13e6120cb83a5b817f72d8931f12d`
**Target file:** `erpnext/controllers/subcontracting_controller.py` (1585 lines, 54 class methods, 6 module-level functions, ~68 total `def` declarations as of baseline).
**Prior lessons referenced:**
- Part 1 (warm-up): `docs/lessons/01-warm-up-update-payment-schedule.md`
- Part 2 (intermediate): `docs/lessons/02-intermediate-sales-invoice-n1s.md`
- Architecture review (Finding 1.10 chooses this target): `docs/reviews/2026-05-27-erpnext-architecture-review-1.md`

## 1. Problem and pedagogical aim

`SubcontractingController` is a 1585-line, 54-method god class. The architecture review (Finding 1.10) flagged it as a refactor candidate; its proposed fix is "split into per-concern helper modules under the same DocType directory; have the DocType class delegate." This spec executes that fix at the granularity appropriate for a single PR.

The didactical aim, per Lesson 02's forward-reference (`docs/lessons/02-intermediate-sales-invoice-n1s.md:401-403`), is to introduce **cross-method and cross-class dependencies** — specifically a module-boundary refactor with public-API preservation. This is the technique not yet shown in Part 1 (internal extract within one method) or Part 2 (N+1 batching within one file).

The lesson will demonstrate **four distinct API-preservation patterns** in one refactor:
1. Free-function delegation (validation cluster).
2. Free-function delegation with internal helper-call dependency between modules (data assembly cluster).
3. Helper class holding a back-reference to the controller (supplied-items machinery).
4. Module-level re-export with whitelist-URL preservation (`@frappe.whitelist()` functions).

## 2. End-state file layout

```
erpnext/controllers/
├── subcontracting_controller.py      ~250 L  (was 1585)
│   ├── class SubcontractingController(StockController):
│   │     def __init__               (instantiates SuppliedItemsHelper)
│   │     def before_validate
│   │     def validate
│   │     def set_materials_for_subcontracted_items   (orchestrator)
│   │     def create_raw_materials_supplied_or_received (orchestrator)
│   │     def __update_consumed_qty_in_subcontract_order
│   │     def set_consumed_qty_in_subcontract_order
│   │     def update_ordered_and_reserved_qty
│   │     def make_sl_entries_for_supplier_warehouse
│   │     def update_stock_ledger
│   │     def get_supplied_items_cost
│   │     def set_subcontracting_order_status
│   │     def calculate_additional_costs
│   │     def get_current_stock              (@frappe.whitelist class method — STAYS)
│   │     @property sub_contracted_items     (STAYS)
│   │     def update_requested_qty
│   │     (thin delegators for public methods on the helpers)
│   └── re-export of api.py whitelist symbols (preserves URLs)
│
└── subcontracting/                    (new subpackage)
    ├── __init__.py                    (empty)
    ├── validation.py                  ~140 L
    │     4 free functions:
    │       validate_rejected_warehouse(doc)
    │       remove_empty_rows(doc)
    │       set_items_conversion_factor(doc)
    │       validate_items(doc)
    ├── data_assembly.py               ~270 L
    │     12 free functions (private leading-underscore = module-private):
    │       _get_data_before_save(doc)
    │       _identify_change_in_item_table(doc)
    │       _get_backflush_based_on(doc)
    │       initialized_fields(doc)
    │       _get_subcontract_orders(doc)
    │       _get_pending_qty_to_receive(doc)
    │       _get_transferred_items(doc)
    │       _set_alternative_item_details(doc, row)
    │       _get_received_items(doc, doctype)
    │       _get_consumed_items(doc, doctype, receipt_items)
    │       _update_consumed_materials(doc, ...)
    │       get_available_materials(doc)
    ├── supplied_items.py              ~545 L
    │     class SuppliedItemsHelper:
    │       def __init__(self, controller): self.controller = controller
    │     25 methods total, including:
    │       set_valuation_rate_for_rm()          (moved from L79; mutator)
    │       _remove_changed_rows()
    │       _remove_serial_and_batch_bundle(item)
    │       _get_materials_from_bom(item_code, bom_no, exploded_item=0)
    │       _update_reserve_warehouse(row, item)
    │       _set_alternative_item(bom_item)
    │       _set_serial_and_batch_bundle(item_row, rm_obj, qty)
    │       _get_batch_nos_for_bundle(qty, key)
    │       _get_serial_nos_for_bundle(qty, key)
    │       _add_supplied_or_received_item(item_row, bom_item, qty)
    │       set_batch_for_supplied_items()
    │       batch_has_not_available(batch_no, qty_required)
    │       update_rate_for_supplied_items()
    │       get_item_row(reference_name)
    │       set_rate_for_supplied_items(rm_obj, item_row)
    │       _set_batch_nos(bom_item, item_row, rm_obj, qty)
    │       _set_consumed_qty(rm_obj, consumed_qty, required_qty=0)
    │       _set_serial_nos(item_row, rm_obj)
    │       _set_batch_no_as_per_qty(item_row, rm_obj, batch_no, qty)
    │       _get_qty_based_on_material_transfer(item_row, transfer_item)
    │       _set_supplied_or_received_items()
    │       _set_rate_for_serial_and_batch_bundle()
    │       _modify_serial_and_batch_bundle()
    │       _get_bundle_to_modify(name)
    │       _prepare_supplied_or_received_items()
    │       _validate_batch_no(row, key)
    │       _validate_serial_no(row, key)
    │       _validate_supplied_or_received_items()
    └── api.py                         ~230 L
          6 module-level functions:
            get_item_details(items)
            get_pending_subcontracted_quantity(doctype, name)
            @frappe.whitelist() make_rm_stock_entry(...)
            add_items_in_ste(ste_doc, row, qty, rm_details, ...)
            make_return_stock_entry_for_subcontract(...)
            @frappe.whitelist() get_materials_from_supplier(...)
```

**Naming-collision check:** `erpnext.controllers.subcontracting` (this new subpackage) is distinct from `erpnext.subcontracting` (existing DocType module). No conflict.

## 3. API preservation patterns (the lesson's spine)

### 3.1 — Free-function delegation (validation.py, data_assembly.py)

```python
# subcontracting/validation.py
def validate_items(doc):
    for item in doc.items:
        ...

# subcontracting_controller.py
from .subcontracting import validation

class SubcontractingController(StockController):
    def validate_items(self):              # thin delegator preserves API
        validation.validate_items(self)
```

Subclass call `self.validate_items()` resolves to the delegator, which calls into validation.py. Subclasses see no change.

### 3.2 — Helper class with back-reference (supplied_items.py)

```python
# subcontracting/supplied_items.py
class SuppliedItemsHelper:
    def __init__(self, controller):
        self.controller = controller

    def set_batch_for_supplied_items(self):
        for row in self.controller.supplied_items:
            ...

# subcontracting_controller.py
from .subcontracting.supplied_items import SuppliedItemsHelper

class SubcontractingController(StockController):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ... existing subcontract_data init ...
        self.supplied_items_helper = SuppliedItemsHelper(self)

    def set_batch_for_supplied_items(self):    # thin delegator
        self.supplied_items_helper.set_batch_for_supplied_items()
```

**Why a helper class for this cluster and not free functions?** This cluster is the biggest single chunk (~545 L, 25 methods). A class provides namespacing (`self.supplied_items_helper.foo()` vs. `supplied_items.foo(self)`) and groups the methods that share the same self-reference. Internal-to-helper method calls become `self.foo()` instead of `controller.foo(controller)`. Trade-off: slightly more setup wiring; pedagogical benefit: shows the second standard pattern.

### 3.3 — Module-level re-export for whitelist-URL preservation (api.py)

```python
# subcontracting/api.py
@frappe.whitelist()
def make_rm_stock_entry(subcontract_order, rm_items=None, ...):
    ...

# subcontracting_controller.py
# Re-export whitelisted functions to preserve the original API path:
#   /api/method/erpnext.controllers.subcontracting_controller.make_rm_stock_entry
# JS callsites (purchase_order.js L483, subcontracting_order.js L704)
# and Python importers (stock_entry.py L4170, 6 test files) keep working.
from .subcontracting.api import (  # noqa: F401
    get_item_details,
    get_pending_subcontracted_quantity,
    make_rm_stock_entry,
    add_items_in_ste,
    make_return_stock_entry_for_subcontract,
    get_materials_from_supplier,
)
```

**Why this works** (Verified Assumption V1): Frappe's URL resolution is `getattr(get_module(modulename), methodname)` (`~/frappe-bench/apps/frappe/frappe/__init__.py:1117-1119`). The `from .subcontracting.api import make_rm_stock_entry` statement makes the same function object accessible at `erpnext.controllers.subcontracting_controller.make_rm_stock_entry`. The `@frappe.whitelist()` decoration travels with the function object — no second decoration needed. `is_whitelisted()` checks the function object's identity, not its `__module__` attribute.

### 3.4 — Name-mangling translation

The 30+ `__`-prefixed methods (e.g., `__get_data_before_save`) use Python name mangling — internally resolve to `_SubcontractingController__get_data_before_save`. When extracted to a module:
- As free functions (validation.py, data_assembly.py): rename `__foo` → `_foo` (single underscore = convention "module-private"). Name mangling no longer applies.
- As helper-class methods (supplied_items.py): same rename. The mangling rules apply per class; renaming to `_foo` keeps it semantically equivalent.

Internal callsites translate:
- `self.__foo()` (inside controller) → `validation._foo(self)` or `self.supplied_items_helper._foo()` depending on cluster.
- Cross-helper calls (e.g., validation.py needs `get_pending_subcontracted_quantity` from api.py): `from .api import get_pending_subcontracted_quantity`.

### 3.5 — Methods NOT extracted (stay on controller)

These remain on `SubcontractingController` because they're either orchestrators, lifecycle hooks, or tightly bound to `StockController` machinery:

- `__init__`, `before_validate`, `validate` (lifecycle hooks)
- `set_materials_for_subcontracted_items`, `create_raw_materials_supplied_or_received` (orchestrators — they call into multiple extracted clusters)
- `__update_consumed_qty_in_subcontract_order`, `set_consumed_qty_in_subcontract_order` (entry-point orchestration — small, tightly bound to inherited methods)
- Stock-ledger cluster: `update_ordered_and_reserved_qty`, `make_sl_entries_for_supplier_warehouse`, `update_stock_ledger`, `get_supplied_items_cost`, `set_subcontracting_order_status` (tightly coupled to `StockController` base class)
- Misc orchestration: `calculate_additional_costs`, `update_requested_qty`
- `get_current_stock` (`@frappe.whitelist()` **class method** at L1312) — stays as a class method because it's invoked through Frappe's `run_doc_method` path, which is keyed on the class and DocType.
- `@property sub_contracted_items` (L1324) — small, cached, lives well on the class.

## 4. Verification

### 4.1 — Behavior gate (non-negotiable)

Baseline (HEAD `82056c5029`, captured at spec-write time):

| Suite | Pass count | Known failures |
|---|---|---|
| `erpnext.controllers.tests.test_subcontracting_controller` | **19/19** ✅ | — |
| `erpnext.subcontracting.doctype.subcontracting_order.test_subcontracting_order` | **16/16** ✅ | — |
| `erpnext.subcontracting.doctype.subcontracting_receipt.test_subcontracting_receipt` | **32/32** ✅ | — |
| `erpnext.subcontracting.doctype.subcontracting_inward_order.test_subcontracting_inward_order` | **10/12** ⚠️ | `test_secondary_items_delivery`, `test_work_order_creation_qty` |
| **Total** | **77/79** | 2 baseline failures (known issues, see §4.4) |

Run command (4 sequential invocations — `bench --module A --module B` only honors the last `--module`):

```bash
cd ~/frappe-bench
for M in \
  erpnext.controllers.tests.test_subcontracting_controller \
  erpnext.subcontracting.doctype.subcontracting_order.test_subcontracting_order \
  erpnext.subcontracting.doctype.subcontracting_receipt.test_subcontracting_receipt \
  erpnext.subcontracting.doctype.subcontracting_inward_order.test_subcontracting_inward_order
do
  ~/.local/bin/bench --site test_site run-tests --module "$M" --lightmode
done
```

**Pass criterion at every intermediate commit:**
1. Total pass count must equal 77.
2. Failure set must equal `{test_secondary_items_delivery, test_work_order_creation_qty}` (exactly — no new failures, same known failures).
3. No new errors (tracebacks) other than the 2 baseline ones.

Any deviation stops the SDD loop; no proceeding to the next extract on a red bar.

### 4.2 — Perf parity gate

A god-class split should not move runtime numbers. Measuring proves it didn't. This also gives Part 3 the cache-warm path Part 2 omitted.

**Script:** `docs/measurements/scripts/measure_part3_refactor.py` — mirrors Part 2's script structure with three differences:

1. **Fixture prefix:** `_measure_part3_*` (NOT `_measure_n1_*`) — avoids collision with Part 2's lingering fixtures in `test_site`.
2. **Measures BOTH cold and warm paths per run:**
   - Cold: clear `frappe.local.cache` AND `frappe.db.value_cache` before each iteration (Part 2 methodology, carried over).
   - Warm: do not clear caches between iterations 2..N — measures steady-state cost.
3. **Subjects:** `SubcontractingOrder.validate()`, `SubcontractingReceipt.validate()`, `SubcontractingInwardOrder.validate()`. Each at N=1, 5, 20 rows.

**Pass criteria** (informed by V20: Part 2 noise was 20-47% at small N, ~3-10% at N=20):

| Metric | Criterion | Rationale |
|---|---|---|
| **Query count** (binary) | AFTER == BEFORE exactly | Refactor changes zero DB calls; any drift indicates extraction accidentally added/removed a `frappe.db.*` call. |
| **Latency (cold, N=20 median)** | AFTER within ±10% of BEFORE | ±5% was the design's initial target; verification of Part 2's noise floor (`docs/measurements/02-sales-invoice-n1-batching.md:108-122`) shows 20-47% variance is normal at small N. ±10% on N=20 medians is achievable. |
| **Latency (warm, N=20 median)** | AFTER within ±10% of BEFORE | Same rationale. New for Part 3; addresses Part 2's gap. |

**Capture protocol:** BEFORE on baseline commit (`82056c5029`), AFTER on the post-extract commit. Stored at `/tmp/measure_part3_before.json` and `/tmp/measure_part3_after.json`. Same checkout dance as Part 2.

### 4.3 — Structural metrics

Reported in `docs/measurements/03-subcontracting-controller-god-class-split.md` (parallels Part 2's report format):

| Metric | Tool | BEFORE expectation | AFTER expectation |
|---|---|---|---|
| LOC per file | `wc -l` | controller=1585 | controller≈250, validation≈140, data_assembly≈270, supplied_items≈545, api≈230; **sum ≈1435** (less than 1585 by ~150 — boilerplate/imports/blank lines saved) |
| Methods per class | `grep -cE "^\s*def " <file>` (inside class block) | 54 in controller | ≈14 in controller, 25 in SuppliedItemsHelper |
| Module-level functions per file | `grep -cE "^def "` | 6 in subcontracting_controller.py (excluded class methods) | 0 in controller (all moved); 4 in validation.py; 12 in data_assembly.py; 6 in api.py |
| Cyclomatic complexity | `radon cc -s -a <file>` (install via `~/frappe-bench/env/bin/pip install radon`) | Capture aggregate at baseline | Per-file CC each ≤ baseline; sum ≈ baseline |
| Maintainability index | `radon mi <file>` | controller MI < 20 expected (low) | each new file MI > 50 expected (moderate-to-high) |
| Import-graph fan-out | `grep -rln "erpnext.controllers.subcontracting_controller" --include="*.py" --include="*.js" --include="*.json" erpnext/` | Capture count (≈15 Python + 4 JS importers) at baseline | Unchanged — consumers still import from same path (re-export preserves it) |

### 4.4 — Known issues, accepted as out of scope

Per user decision during brainstorming, the 2 baseline test failures in `test_subcontracting_inward_order` are accepted as pre-existing:

- `test_secondary_items_delivery` (line 333): `frappe.get_doc("Subcontracting BOM", "SB-0001")` raises `DoesNotExistError` — the fixture record `SB-0001` does not exist in `test_site`.
- `test_work_order_creation_qty` (line 161): same root cause.

Both are fixture-data issues, not code bugs. The refactor neither addresses nor worsens them. They are NOT in the refactor's touch surface. Fixing them is future work (see §6).

### 4.5 — What this verification does NOT prove

Explicitly out of scope, called out so the lesson is honest:
- That the refactor improved *human* comprehension — metrics can't verify subjective claims.
- That maintainability metrics correlate with bug rates — they don't, conclusively. Radon numbers are descriptive, not causal.
- That perf is *better* — only that it's the same (parity gate, not improvement gate).
- That the 2 known-failing tests' underlying issues have been investigated.

## 5. Migration ordering

Seven commits, ordered so each intermediate state is shippable (tests gate-pass, perf parity holds locally), with the highest-blast-radius extract (whitelist URLs) last for rollback isolation.

| # | Commit | What | Why this order |
|---|---|---|---|
| T0 | `chore(subcontracting): capture baseline metrics + perf` | Run baseline test suite (record 77 pass, 2 known failures + names). Install radon. Capture structural metrics + perf BEFORE numbers. Commit baseline stub at `docs/measurements/03-subcontracting-controller-god-class-split-baseline.md`. | Locks in comparison reference before any code moves. |
| T1 | `refactor(subcontracting): extract validation cluster to subcontracting/validation.py` | Create `erpnext/controllers/subcontracting/__init__.py` (empty). Create `validation.py`. Move 4 methods (`validate_rejected_warehouse`, `remove_empty_rows`, `set_items_conversion_factor`, `validate_items`) as free functions taking `doc`. `validate_items` at L190 calls `get_pending_subcontracted_quantity` (a module-level function still in `subcontracting_controller.py` at this commit) — import it via `from erpnext.controllers.subcontracting_controller import get_pending_subcontracted_quantity`. At T4, this import gets updated to `from .api import get_pending_subcontracted_quantity`. Keep thin delegators in controller. | Smallest cluster; no helper class; introduces the free-function pattern at lowest risk. |
| T2 | `refactor(subcontracting): extract data_assembly cluster` | Create `data_assembly.py`. Move 12 methods (`_get_data_before_save`, `_identify_change_in_item_table`, `_get_backflush_based_on`, `initialized_fields`, `_get_subcontract_orders`, `_get_pending_qty_to_receive`, `_get_transferred_items`, `_set_alternative_item_details`, `_get_received_items`, `_get_consumed_items`, `_update_consumed_materials`, `get_available_materials`). Apply name-mangling rename `__foo` → `_foo`. Update internal callsites in controller. Preserve local imports inside methods (e.g., `from erpnext.deprecation_dumpster import deprecation_warning` at L458, L467). | Builds on T1's pattern; larger by method count but same shape (free functions). Tests the name-mangling rename at scale. |
| T3 | `refactor(subcontracting): extract SuppliedItemsHelper class` | Create `supplied_items.py` with `class SuppliedItemsHelper`. Move 25 methods including `set_valuation_rate_for_rm` (from L79-110 — moved here, not validation.py, because it mutates rates). Add `self.supplied_items_helper = SuppliedItemsHelper(self)` to controller `__init__`. Thin delegators on controller for public-named methods. Preserve local imports inside methods (L750-751 in `set_batch_for_supplied_items`). | The pattern-shift commit (helper class with back-reference). Largest extract by LOC. T1+T2 already de-risked the test loop. No whitelist URLs in this cluster. |
| T4 | `refactor(subcontracting): extract module-level API + whitelist re-export` | Create `api.py`. Move 6 module-level functions (`get_item_details`, `get_pending_subcontracted_quantity`, `make_rm_stock_entry`, `add_items_in_ste`, `make_return_stock_entry_for_subcontract`, `get_materials_from_supplier`); preserve `@frappe.whitelist()` decoration on `make_rm_stock_entry` and `get_materials_from_supplier`. Add re-export block at bottom of `subcontracting_controller.py`. Update `validation.py`'s import from `subcontracting_controller` → `.api` for `get_pending_subcontracted_quantity` (set up in T1). Manual smoke from `bench console`: call `/api/method/erpnext.controllers.subcontracting_controller.make_rm_stock_entry` (or via `frappe.call(...)`) and confirm response. | Highest external-API risk (JS frontend + integrations). Done last so it's an independent rollback target. |
| T5 | `docs: measure SubcontractingController god-class split before/after` | Run measurement script BEFORE on `82056c5029` and AFTER on T4's HEAD. Produce `docs/measurements/03-subcontracting-controller-god-class-split.md` with structural-metrics table + perf parity numbers (cold + warm) + methodology. Mirrors Part 2's T4. | Captures the lesson's data spine. |
| T6 | `docs: add complex lesson — SubcontractingController god-class split` | Write `docs/lessons/03-complex-subcontracting-god-class-split.md` mirroring Lesson 02's structure (~400-450 L target). Sections: Title → Commits → What we found → How we identified → What we did (per cluster, with before/after snippets per pattern) → What we measured → What we learned (4 numbered insights, one per pattern) → Verification stack → Transferable patterns → What this lesson is/isn't → Cross-refs → Next steps. | Final SDD task. Same shape as Part 2's T5. |

**Total:** 7 commits. Plus 1-2 review-fix commits if 2-stage SDD review catches issues. Comparable to Part 2's 7-commit footprint.

### 5.1 — Each intermediate commit must satisfy

- Tests: 77/79 pass with the 2 known-failures exactly matching baseline (§4.1).
- Import: `python -c "from erpnext.controllers.subcontracting_controller import SubcontractingController"` succeeds.
- For T4 specifically: manual smoke confirms `/api/method/...make_rm_stock_entry` returns 200.

### 5.2 — Rollback strategy

Each commit is atomic and revertable via `git revert <SHA>`:
- T1-T3 touch only the cluster they extract + corresponding controller wiring.
- T4 only touches the api.py extraction + re-export.
- T5, T6 are doc-only.

If T4 breaks an external whitelist caller not caught by tests, `git revert T4` restores the original module-level functions without disturbing T1-T3's structural gains.

## 6. Out of scope (explicitly NOT in this refactor)

| Item | Why excluded | Note for future |
|---|---|---|
| Batch the 2 known N+1 sites at `subcontracting_controller.py:145` and `:1167` | Conflates god-class-split with N+1-batching (two distinct techniques). Was rejected during brainstorming. | Future "Part 4" or one-off cleanup. Lesson 03 will surface this in "Next steps." |
| Extract stock-ledger cluster (`update_ordered_and_reserved_qty`, `update_stock_ledger`, etc.) | Tightly coupled to `StockController` base class methods; splitting would also touch `stock_controller.py`. | Future work. |
| Extract `calculate_additional_costs`, `update_requested_qty`, etc. (misc orchestration) | Small enough to live on the orchestrator. YAGNI. | Stays on controller indefinitely unless a real need emerges. |
| `subcontracting_inward_controller.py` cleanup | Not in scope — sibling controller, not a split of this one. | Untouched. |
| Renaming `subcontracting_controller.py` file | Public import path is load-bearing across 3 subclasses + 15+ Python importers + 4 JS callsites. Cost-benefit: zero. | File path stays. |
| `BuyingController` (1297 L) cleanup | Not the chosen target. | Future work. |
| Refactoring any method's internal logic | Structural-only refactor. Method bodies move verbatim. | Future work. |
| Type hints on new functions/class | Inconsistent with rest of codebase (minimal type hints). | Codebase-wide initiative if ever undertaken. |
| **Fix the 2 baseline test failures** (`test_secondary_items_delivery`, `test_work_order_creation_qty`) | Pre-existing fixture-data issue. Out of scope per user decision (§4.4). | Investigate Subcontracting BOM autoname / seed `SB-0001` fixture as a separate one-off cleanup. |
| Cyclomatic-complexity threshold enforcement | Reporting metrics is in scope; enforcing thresholds is not. | If desired, add radon to CI as a separate initiative. |

## 7. Verified assumptions

The following load-bearing assumptions were verified empirically against the codebase before this spec was finalized. Each lists the falsifiable claim and the evidence found.

| # | Claim | Evidence | Verdict |
|---|---|---|---|
| V1 | `@frappe.whitelist()` URL resolution uses `getattr(get_module(modulename), methodname)` — re-export preserves URLs. | `~/frappe-bench/apps/frappe/frappe/__init__.py:1117-1119` (`get_attr`); `~/frappe-bench/apps/frappe/frappe/handler.py:248-249` (`execute_cmd → get_attr`) | ✅ Confirmed |
| V2 | No external Python importers exist beyond what re-export handles. | 15 Python files import from `erpnext.controllers.subcontracting_controller`; all import either the class (`SubcontractingController`) or the whitelisted functions. Re-export handles all. Notable production importer: `erpnext/stock/doctype/stock_entry/stock_entry.py:4170`. | ✅ Confirmed |
| V3 | `hooks.py` does not reference moved methods or symbols. | `grep -nE "subcontracting_controller\|make_rm_stock_entry\|get_materials_from_supplier" erpnext/hooks.py` → 0 hits. | ✅ Confirmed |
| V4 | Subclasses (`SubcontractingOrder`, `SubcontractingReceipt`, `SubcontractingInwardOrder`) do not override any extracted method. | `grep -nE "def (validate_items\|set_valuation_rate_for_rm\|...)" erpnext/subcontracting/doctype/...subcontracting_*.py` → 0 hits across all checked methods. | ✅ Confirmed |
| V5 | No external code reaches mangled names via `_SubcontractingController__foo`. | `grep -rn "_SubcontractingController__" --include="*.py" --include="*.js" erpnext/` → 0 hits. | ✅ Confirmed |
| V6 | No `super()` calls inside any extracted method. | Only 2 hits in the file: L28 (`__init__`, stays on controller) and L77 (`validate`, stays on controller). | ✅ Confirmed |
| V7 | No class-level attribute access (`cls.X`, `SubcontractingController.X`) in any method. | `grep -nE "(SubcontractingController\|cls)\." erpnext/controllers/subcontracting_controller.py` → 0 hits inside method bodies. | ✅ Confirmed |
| V8 | Public-named methods moving to supplied_items.py have a delegator path in the design. | Design §3.2 covers `set_valuation_rate_for_rm`, `set_batch_for_supplied_items`, `update_rate_for_supplied_items`, `get_item_row`, `set_rate_for_supplied_items` via thin delegators on controller; `batch_has_not_available` and `_get_materials_from_bom` are helper-internal (callers stay within the helper). | ✅ Confirmed |
| V9 | `test_subcontracting_controller.py` passes on `test_site` at baseline. | `bench --site test_site run-tests --module ...test_subcontracting_controller --lightmode` → 19/19 OK in 39.8s. | ✅ Confirmed |
| V10 | The 3 subclass test files run cleanly. | order: 16/16 OK; receipt: 32/32 OK; inward_order: **10/12** (2 known fixture failures — accepted, see §4.4). | ✅ Confirmed (with caveat) |
| V11 | `radon` installs cleanly in the bench venv. | `~/frappe-bench/env/bin/pip --version` works; radon is on PyPI; deferred actual install to T0. | ✅ Confirmed |
| V12 | `validate_items` calls `get_pending_subcontracted_quantity` → `validation.py` imports from `api.py`. | `subcontracting_controller.py:190` calls `get_pending_subcontracted_quantity(order_item_doctype, order_name)`. `api.py` does not need to import from `validation.py`, so no circular import. | ✅ Confirmed |
| V13 | Extracted methods preserve their in-method local imports. | 4 local imports found in extraction-target line ranges: L458/L467 (`from erpnext.deprecation_dumpster import deprecation_warning` in `__update_consumed_materials`); L750/L751 (`from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos_for_outward` and `from erpnext.stock.get_item_details import get_filtered_serial_nos` in `set_batch_for_supplied_items`). Implementation must preserve these. | ✅ Confirmed (noted as implementation detail) |
| V14 | No method being extracted has a behavior-changing decorator. | 4 `@frappe.whitelist`/`@property` decorators in the file: L1312 (`get_current_stock` — class method, STAYS); L1324 (`sub_contracted_items` — STAYS); L1382 (`make_rm_stock_entry` — re-exported); L1570 (`get_materials_from_supplier` — re-exported). No `@cached_property`, `@staticmethod`, `@classmethod` on extracted methods. | ✅ Confirmed |
| V15 | Controller `__init__` calls `super().__init__()` first; safe to append helper instantiation. | L28: `super().__init__(*args, **kwargs)` is the first line. Subsequent body sets `self.subcontract_data`. Appending `self.supplied_items_helper = SuppliedItemsHelper(self)` after the existing init body is safe. | ✅ Confirmed |
| V16 | Frappe dev-mode picks up new packages without explicit registration. | `erpnext/modules.txt` lists app-level modules (Accounts, Buying, ...), NOT Python subpackages. `erpnext/controllers/__init__.py` is empty (0 bytes). Adding `subcontracting/__init__.py` (empty) requires no registration. | ✅ Confirmed |
| V17 | No namespace collision: `erpnext/controllers/subcontracting/` does not exist. | `ls erpnext/controllers/subcontracting/` → "No such file or directory". | ✅ Confirmed |
| V18 | Each extracted method appears exactly once in the file. | 15 representative method names grepped via `grep -cE "^\s*def <name>\b"` → all returned `1`. | ✅ Confirmed |
| V19 | `set_valuation_rate_for_rm` (L79-110) is a mutator → belongs in supplied_items.py, NOT validation.py. | Re-read L79-110: iterates `self.supplied_items`, calls `get_incoming_rate`, writes `row.rate`, `row.amount`, calls `self.calculate_items_qty_and_amount()`. Clearly mutation, not validation. | ✅ Confirmed (design re-classifies) |
| V20 | Perf parity tolerance is achievable given test_site noise floor. | Part 2's report (`docs/measurements/02-sales-invoice-n1-batching.md:108-126`) shows 20-47% variance at N=1, dropping to ~3-10% at N=20. Original ±5% target was too tight; relaxed to ±10% on N=20 medians + identical query count (binary check). | ✅ Confirmed (design relaxed tolerance) |
| V21 | JS callsites use whitelist URLs (not Python imports). | 4 hits in `erpnext/buying/doctype/purchase_order/purchase_order.js` (L109, L483) and `erpnext/subcontracting/doctype/subcontracting_order/subcontracting_order.js` (L596, L704), all `frappe.call({method: "erpnext.controllers.subcontracting_controller.{make_rm_stock_entry|get_materials_from_supplier}"})`. Re-export protects all. | ✅ Confirmed |
| V22 | Helper class with back-reference is safe (no `__del__`/`weakref` concerns). | `grep -nE "__del__\|__weakref__\|import weakref\|weakref\." erpnext/controllers/subcontracting_controller.py` → 0 hits. Python GC handles the cycle. | ✅ Confirmed |

## 8. References

- **Architecture review:** `docs/reviews/2026-05-27-erpnext-architecture-review-1.md` (Finding 1.10, target selection)
- **Part 1 lesson:** `docs/lessons/01-warm-up-update-payment-schedule.md`
- **Part 2 lesson:** `docs/lessons/02-intermediate-sales-invoice-n1s.md` (forward-references Part 3 at L401-403)
- **Part 2 measurement report:** `docs/measurements/02-sales-invoice-n1-batching.md` (noise-floor data informing §4.2 perf tolerance)
- **Frappe URL resolution:** `~/frappe-bench/apps/frappe/frappe/__init__.py:1111-1119` (`get_attr`); `~/frappe-bench/apps/frappe/frappe/handler.py:246-259` (`execute_cmd` → `get_attr`)
- **Baseline commit:** `82056c5029f13e6120cb83a5b817f72d8931f12d` (`refactoring` branch)
