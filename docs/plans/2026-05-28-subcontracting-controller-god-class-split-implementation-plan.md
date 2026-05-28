# SubcontractingController God-Class Split Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Source spec:** `docs/specs/2026-05-28-subcontracting-controller-god-class-split-design.md` (commit SHA: `e7220d7a19d37a442424aa249b77a12a14d069e6`)

**Goal:** Split `erpnext/controllers/subcontracting_controller.py` (1585 L, 54-method god class) into a slim orchestrator (~250 L) plus four per-concern modules under a new `erpnext/controllers/subcontracting/` subpackage, while preserving 100% of the public API (importers, subclass dispatch, `@frappe.whitelist()` URL paths) and demonstrating four distinct API-preservation patterns in one commit series.

**Architecture:** Seven commits. T0 captures the baseline. T1-T4 each extract one cluster (validation free-functions → data-assembly free-functions → SuppliedItemsHelper class → module-level API + whitelist re-export), with thin delegators on the controller and atomic body rewrites of three stays-on-controller orchestrators (per spec §3.6). T5 measures structural + perf parity (cold + warm) and produces the report. T6 writes the lesson.

**Tech stack:** Python 3.14 (bench venv), Frappe v16 framework (`@frappe.whitelist()`, `frappe.get_attr`, `frappe.local.cache`, `frappe.db.value_cache`), MariaDB via Frappe Bench (`~/frappe-bench/`), radon 6.x for cyclomatic-complexity / maintainability-index metrics.

---

## File Structure

**Create:**
- `erpnext/controllers/subcontracting/__init__.py` — empty package marker.
- `erpnext/controllers/subcontracting/validation.py` (~140 L) — 4 free functions taking `doc`.
- `erpnext/controllers/subcontracting/data_assembly.py` (~270 L) — 12 free functions taking `doc` (module-private leading-underscore for moved `__`-prefixed methods).
- `erpnext/controllers/subcontracting/supplied_items.py` (~545 L) — `class SuppliedItemsHelper(controller)` with 25 methods.
- `erpnext/controllers/subcontracting/api.py` (~230 L) — 6 module-level functions including 2 `@frappe.whitelist()`-decorated ones.
- `docs/measurements/scripts/measure_part3_refactor.py` — re-runnable cold+warm measurement script.
- `docs/measurements/03-subcontracting-controller-god-class-split-baseline.md` — T0 baseline stub (structural metrics + BEFORE perf numbers).
- `docs/measurements/03-subcontracting-controller-god-class-split.md` — T5 final report (BEFORE/AFTER, cold + warm, parity verdict).
- `docs/lessons/03-complex-subcontracting-god-class-split.md` — T6 pedagogical write-up (~400-450 L target).

**Modify:**
- `erpnext/controllers/subcontracting_controller.py` (1585 → ~250 L) — `__init__` instantiates `SuppliedItemsHelper`; thin delegators for moved public-named methods; 6 stays-on-controller body rewrites per spec §3.6; module-level re-export block for whitelist-URL preservation.

**No test changes** — this is a structural-only refactor. Spec §6 excludes refactoring any method's internal logic. The 77/79 baseline test suite is the behavior gate at every intermediate commit.

## Inherited from spec

The following assumptions were verified by `thorough-brainstorming` at spec-write time and are NOT re-verified here. Trusted as ground truth:

- **V1**: `@frappe.whitelist()` URL resolution uses `getattr(get_module(modulename), methodname)` (`~/frappe-bench/apps/frappe/frappe/__init__.py:1117-1119`); re-export preserves URLs without re-decoration.
- **V2**: All Python importers from `erpnext.controllers.subcontracting_controller` import either the class or the whitelisted functions; re-export handles all.
- **V3**: `hooks.py` does not reference moved methods or symbols (`grep` returned 0 hits).
- **V4**: 3 subclasses do not override any extracted method (full sweep of method names).
- **V5**: No external code reaches mangled names via `_SubcontractingController__*` (0 hits across erpnext/).
- **V6**: No `super()` calls inside any extracted method (only L28 and L77, both stay).
- **V7**: No class-level attribute access (`cls.X`, `SubcontractingController.X`) inside any method body.
- **V8**: Public-named methods moving to supplied_items.py have a delegator path in the design.
- **V9-V10**: Baseline test pass counts (77/79 with 2 named known failures) at HEAD `82056c5029`.
- **V11**: `radon` installs cleanly in the bench venv.
- **V12**: `validate_items` calls `get_pending_subcontracted_quantity`; no circular import (api.py never imports validation.py).
- **V13**: Extracted methods preserve their in-method local imports (L458, L467, L750, L751).
- **V14**: No method being extracted has a behavior-changing decorator.
- **V15**: Controller `__init__` calls `super().__init__()` first; safe to append helper instantiation.
- **V16**: Frappe dev-mode picks up new subpackages without explicit registration (`__init__.py` empty is sufficient).
- **V17**: No namespace collision — `erpnext/controllers/subcontracting/` does not exist.
- **V18**: Each extracted method appears exactly once in the file.
- **V19**: `set_valuation_rate_for_rm` (L79-110) is a mutator → belongs in supplied_items.py, NOT validation.py.
- **V20**: Perf parity tolerance of ±10% on N=20 medians is achievable given test_site noise floor.
- **V21**: JS callsites use whitelist URLs (not Python imports); re-export protects all 4.
- **V22**: Helper class with back-reference has no `__del__`/`weakref` concerns; Python GC handles the cycle.

## Verified plan-level assumptions

Newly introduced by this plan (paths, signatures, commands, ordering, code-in-plan validity, consumer impact) and verified at plan-write time:

| # | Category | Assumption | Evidence |
|---|---|---|---|
| P1 | File path (new) | `docs/plans/2026-05-28-subcontracting-controller-god-class-split-implementation-plan.md` does not exist | `ls` returned "No such file or directory" |
| P2 | File path (new) | `erpnext/controllers/subcontracting/` does not exist; `docs/measurements/03-*` absent; `docs/lessons/03-*` absent | 3× `ls` returned "No such file or directory" |
| P3 | File path (existing) | `docs/measurements/scripts/` already exists from Part 2; contains `measure_n1_refactor.py` | `ls -ld docs/measurements/scripts/` confirms |
| P4 | Command | `~/.local/bin/bench` (v5.29.1) + `~/frappe-bench/env/bin/pip` (26.1.1, Python 3.14) present and executable | `--version` outputs captured |
| P5 | Command | `bench --site test_site run-tests --module <X> --lightmode` is valid syntax | `bench run-tests --help` lists `--module TEXT` and `--lightmode` |
| P6 | Command | `bench --site test_site console` is a valid Frappe iPython REPL invocation | `bench --site test_site --help` lists `console — Start ipython console for a site` |
| P7 | Command (commit msg) | Codebase uses `<type>(<scope>): <subject>` (`refactor(<scope>):`, `chore(<scope>):`, `docs:`); plan-precedent is `docs: add implementation plan for ...` | `git log --oneline -20` shows both Part 1 (`35b011bd61`) and Part 2 (`3541cb26da`) used this exact pattern |
| P8 | Signature | Controller `__init__` at L27, `super().__init__(*args, **kwargs)` at L28 (first statement); `set_valuation_rate_for_rm` at L79 (mutator on `self.supplied_items`, writes `row.rate`/`row.amount`); `validate_items` body at L190 calls `get_pending_subcontracted_quantity(order_item_doctype, order_name)` | Direct Read of L1-115 and L180-215 |
| P9 | Signature (§3.6 callsites) | `set_materials_for_subcontracted_items` L1109 = `self.__identify_change_in_item_table()`; L1110 = `self.__prepare_supplied_or_received_items()`; L1111 = `self.__validate_supplied_or_received_items()`; `set_consumed_qty_in_subcontract_order` L1143 = `self.__get_subcontract_orders()`; L1151 = `self.__update_consumed_materials(doctype, return_consumed_items=True)`; L1159 = `self.__update_consumed_qty_in_subcontract_order(itemwise_consumed_qty)`; `set_subcontracting_order_status` L1275 = `self.__get_subcontract_orders` (no parens — dead-line) | Direct Read of L1100-1175 + L1268-1290 |
| P10 | Signature (stays) | `__update_consumed_qty_in_subcontract_order` defined at L1120 (STAYS — both ends of L1159 callsite share class scope, mangling resolves) | Direct Read of L1120-1136 |
| P11 | Signature (preserved decorators) | `@frappe.whitelist()` at L1312/`def get_current_stock` (STAYS); `@property` at L1324/`def sub_contracted_items` (STAYS); `@frappe.whitelist()` at L1382/`def make_rm_stock_entry` (MOVES to api.py, decorator preserved); `@frappe.whitelist()` at L1570/`def get_materials_from_supplier` (MOVES to api.py, decorator preserved) | Direct Read of L1305-1335, L1378-1400, L1565-1585 |
| P12 | Signature (preserved in-method local imports) | `from erpnext.deprecation_dumpster import deprecation_warning` at L458 and L467 (inside `__update_consumed_materials`, moves to data_assembly.py); `from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos_for_outward` at L750 and `from erpnext.stock.get_item_details import get_filtered_serial_nos` at L751 (inside `set_batch_for_supplied_items`, moves to supplied_items.py) | Direct Read of L450-485 and L740-770 |
| P13 | Signature (subclass) | `SubcontractingReceipt.__init__` at `subcontracting_receipt.py:99` calls `super().__init__()` at L100 (first statement) — controller's helper instantiation propagates safely | Direct Read of subcontracting_receipt.py L90-105 |
| P14 | Code-in-plan validity | `from __future__` imports: 0 hits in subcontracting_controller.py → no special annotation semantics to propagate to new modules | `grep -n "from __future__"` returned no matches |
| P15 | Code-in-plan validity (Frappe APIs) | `frappe.local.cache = {}` (`frappe/cache_manager.py:298`); `frappe.db.value_cache` and `.clear()` (`frappe/database/database.py:125, 1193`); `frappe.init/connect/destroy` self-bootstrap pattern (`frappe/commands/site.py:396-441`); `frappe.db.sql` is monkey-patchable bound method (`frappe/database/database.py:183`); `frappe.is_whitelisted` (`frappe/__init__.py:462`) | All 5 referenced for the T0 measurement script |
| P16 | Command | `radon` (6.0.1 latest on PyPI) installable in bench venv; `radon cc -s -a <file>` and `radon mi <file>` are stable subcommands since 4.x | `pip index versions radon` returned 6.0.1 |
| P17 | Ordering | T0 first; T1→T2→T3→T4 strictly sequential (all modify `subcontracting_controller.py`); T5 depends on T4 HEAD; T6 depends on T5. SDD must NOT parallelize T1-T4. | All four extraction commits touch the same source file; concurrent SDD subagents would race |
| P18 | Ordering (T1↔T4 import retarget) | T1's `validation.py` imports `get_pending_subcontracted_quantity` from `subcontracting_controller` (function is module-level at L1372 at HEAD — `^def` grep confirmed). T4 retargets to `from .api import ...`. No circular: api.py has no need to import from validation.py. | `grep "^def get_pending_subcontracted_quantity"` matched only at L1372 (module-level) |
| P19 | Ordering (T2/T3 atomic) | The 6 stays-method body rewrites (§3.6) MUST land in the same commit as their callee's extraction. Splitting would create a broken intermediate where mangled lookup `self._SubcontractingController__foo` → AttributeError. T2 commit must include L1109/L1143/L1151 rewrites + L1275 dead-line deletion; T3 commit must include L1110/L1111 rewrites. | Atomicity follows from Python name-mangling semantics |
| P20 | Consumer-impact (Python) | All 9 Python importers from `erpnext.controllers.subcontracting_controller` pull only `SubcontractingController` (the class) or the whitelist functions `make_rm_stock_entry` / `get_materials_from_supplier`. Zero importers pull helper-private method names. Re-export covers 100%. | `grep -rln` + per-file inspection of all 9 importers |
| P21 | Consumer-impact (JS) | 4 JS callsites at exact documented coordinates: `purchase_order.js:109, :483` and `subcontracting_order.js:596, :704`, all `frappe.call({method: "erpnext.controllers.subcontracting_controller.{make_rm_stock_entry\|get_materials_from_supplier}"})`. Re-export at controller bottom preserves all URLs. | `grep -rn "erpnext\.controllers\.subcontracting_controller\." --include="*.js"` |
| P22 | Consumer-impact (construction) | No `__new__` override or bypass on `SubcontractingController` or its 3 subclasses — all construction goes through `__init__`, so the appended `self.supplied_items_helper = SuppliedItemsHelper(self)` always runs and thin delegators are safe. | `grep "SubcontractingController.__new__"` etc. → 0 hits |
| P23 | Code-validity (fixture helpers) | `create_subcontracting_order(**args)` at `test_subcontracting_order.py:826` exists (direct builder). No equivalent `create_subcontracting_receipt` — must compose via `make_subcontracting_receipt` (from `subcontracting_order.py`). No equivalent `create_subcontracting_inward_order` — must build directly via `frappe.get_doc({"doctype": "Subcontracting Inward Order", ...})` or compose from `create_so_scio` + supporting helpers in `test_subcontracting_inward_order.py`. Script-writer notes this in T0's docstring. | `grep -nE "^def (create_\|make_\|new_)"` in all 3 test files |

## Tasks

### Task 0: Capture baseline (tests + structural metrics + perf BEFORE)

**Files:**
- Create: `docs/measurements/scripts/measure_part3_refactor.py`
- Create: `docs/measurements/03-subcontracting-controller-god-class-split-baseline.md`

- [ ] **Step 1: Run baseline 4-suite test gate; record exact pass counts and known-failure names.**
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
Expected: 19/19, 16/16, 32/32, 10/12 (with `test_secondary_items_delivery` and `test_work_order_creation_qty` failing). Total: **77 pass, 2 named known failures**. If anything else fails, **HALT** — environment drift; surface to user before continuing.

- [ ] **Step 2: Install radon into bench venv.**
```bash
~/frappe-bench/env/bin/pip install radon
~/frappe-bench/env/bin/python -c "import radon; print(radon.__version__)"
```

- [ ] **Step 3: Capture structural metrics BEFORE.**
```bash
cd ~/erpnext
wc -l erpnext/controllers/subcontracting_controller.py
grep -cE "^\s*def " erpnext/controllers/subcontracting_controller.py   # methods + module-level functions total
grep -cE "^def "  erpnext/controllers/subcontracting_controller.py     # module-level only
~/frappe-bench/env/bin/radon cc -s -a erpnext/controllers/subcontracting_controller.py
~/frappe-bench/env/bin/radon mi erpnext/controllers/subcontracting_controller.py
grep -rln "erpnext.controllers.subcontracting_controller" --include="*.py" --include="*.js" --include="*.json" erpnext/ | wc -l
```
Record outputs verbatim for the baseline stub.

- [ ] **Step 4: Write `docs/measurements/scripts/measure_part3_refactor.py`.** The script mirrors Part 2's `measure_n1_refactor.py` shape with three differences: (a) measures BOTH cold and warm paths per (subject, N) tuple; (b) subjects are the `validate()` method of `SubcontractingOrder`, `SubcontractingReceipt`, `SubcontractingInwardOrder`; (c) fixture prefix is `_measure_part3_*` (NOT `_measure_n1_*`).

```python
"""Re-runnable measurement: queries + wall-clock for Subcontracting validate() methods.

Measures both cold and warm cache paths. Fixture names use the `_measure_part3_` prefix
to avoid collision with Part 2's lingering `_measure_n1_` fixtures in test_site.

Usage:
    cd ~/frappe-bench
    env/bin/python ~/erpnext/docs/measurements/scripts/measure_part3_refactor.py \\
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


def ensure_fixtures(n_max):
    """Idempotent get-or-create for Items, Suppliers, BOMs, etc. Fixture names prefixed
    with `_measure_part3_` to avoid collision with Part 2's `_measure_n1_` and with
    real test fixtures. Returns dict with per-fixture-type name lists."""
    raise NotImplementedError


def build_subcontracting_order(n, fixtures):
    """Build (and submit) one Subcontracting Order with N items.
    Uses `create_subcontracting_order(**args)` from test_subcontracting_order.py:826 as base;
    extends to N items via `sco.append("items", {...})` before save+submit."""
    raise NotImplementedError


def build_subcontracting_receipt(n, fixtures, sco_name):
    """Build (NOT submitted) one Subcontracting Receipt against a submitted SCO.
    Composes via `make_subcontracting_receipt(sco_name)` (mapper in subcontracting_order.py)."""
    raise NotImplementedError


def build_subcontracting_inward_order(n, fixtures):
    """Build (NOT submitted) one Subcontracting Inward Order with N items.
    Builds via `frappe.get_doc({"doctype": "Subcontracting Inward Order", ...})` directly,
    using fixtures from `create_so_scio` / `make_subcontracted_items` helpers in
    test_subcontracting_inward_order.py."""
    raise NotImplementedError


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
            scr = build_subcontracting_receipt(n, fixtures, sco.name)
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
```

Note: the four `build_*` / `ensure_fixtures` helpers are sketched as `NotImplementedError`. The script-writer fills them at write time by **surveying existing helpers in the 3 test files first** (`create_subcontracting_order`, `make_subcontracting_receipt`, `create_so_scio`, `make_subcontracted_items`, `make_bom_for_subcontracted_items`, etc.). Each builder should be 15-40 lines. The `_measure_part3_*` prefix is mandatory on every fixture record name to prevent collision with Part 2's lingering fixtures and with real test fixtures.

- [ ] **Step 5: Run script to capture BEFORE numbers (tree is at baseline HEAD already).**
```bash
cd ~/frappe-bench
env/bin/python ~/erpnext/docs/measurements/scripts/measure_part3_refactor.py \
    --site test_site --output /tmp/measure_part3_before.json
cat /tmp/measure_part3_before.json
```

- [ ] **Step 6: Write `docs/measurements/03-subcontracting-controller-god-class-split-baseline.md`.** Sections:
  1. **Purpose** — locks in comparison reference before extraction.
  2. **Test baseline** — 4-suite pass counts + the 2 named known failures (from Step 1).
  3. **Structural baseline** — wc/grep/radon outputs from Step 3 in a table.
  4. **Perf baseline** — paste the contents of `/tmp/measure_part3_before.json` inline (or join into a per-(subject, N) table with cold + warm columns).
  5. **Methodology pointer** — link to the measurement script.
  6. **Capture commit SHA** — current HEAD (will be the baseline reference for T5's comparison).

- [ ] **Step 7: Commit.**
```bash
git add docs/measurements/scripts/measure_part3_refactor.py docs/measurements/03-subcontracting-controller-god-class-split-baseline.md
git commit -m "chore(subcontracting): capture baseline metrics + perf"
```

### Task 1: Extract validation cluster to `subcontracting/validation.py`

**Files:**
- Create: `erpnext/controllers/subcontracting/__init__.py` (empty)
- Create: `erpnext/controllers/subcontracting/validation.py` (~140 L)
- Modify: `erpnext/controllers/subcontracting_controller.py` — move 4 method bodies; add `from .subcontracting import validation`; replace bodies with thin delegators.

- [ ] **Step 1: Create the new subpackage marker.**
```bash
mkdir -p erpnext/controllers/subcontracting
: > erpnext/controllers/subcontracting/__init__.py
```

- [ ] **Step 2: Create `validation.py` with the 4 free functions.** Shape:

```python
# erpnext/controllers/subcontracting/validation.py
import frappe
from frappe import _
from frappe.utils import flt

# IMPORTANT: at T1 this import points at the still-module-level function in
# subcontracting_controller.py. T4 retargets it to `from .api import ...`.
from erpnext.controllers.subcontracting_controller import get_pending_subcontracted_quantity


def validate_rejected_warehouse(doc):
    for item in doc.get("items"):
        if flt(item.rejected_qty) and not item.rejected_warehouse:
            if doc.rejected_warehouse:
                item.rejected_warehouse = doc.rejected_warehouse
            # ... rest of body verbatim from controller L111-... ...


def remove_empty_rows(doc):
    # ... body verbatim from controller; replace `self` → `doc` ...
    ...


def set_items_conversion_factor(doc):
    # ... body verbatim ...
    ...


def validate_items(doc):
    # ... body verbatim, including the `get_pending_subcontracted_quantity(...)` call
    # at the original L190 ...
    ...
```

The 4 method bodies are moved **verbatim** with one mechanical substitution: every `self.X` becomes `doc.X`. No internal-logic changes.

- [ ] **Step 3: Replace the 4 method bodies in `subcontracting_controller.py` with thin delegators.** At the top of the file (with the other imports) add:
```python
from erpnext.controllers.subcontracting import validation
```
Then for each of the 4 methods (`validate_rejected_warehouse`, `remove_empty_rows`, `set_items_conversion_factor`, `validate_items`), replace the body with:
```python
def validate_rejected_warehouse(self):
    validation.validate_rejected_warehouse(self)

def remove_empty_rows(self):
    validation.remove_empty_rows(self)

def set_items_conversion_factor(self):
    validation.set_items_conversion_factor(self)

def validate_items(self):
    validation.validate_items(self)
```

These thin delegators preserve the dispatch surface: `before_validate` at L58 still calls `self.remove_empty_rows()` and `self.set_items_conversion_factor()`; `validate` at L67 still calls `self.validate_items()`. Subclasses see no change.

- [ ] **Step 4: Behavior gate — run the 4-suite test block.** Must match baseline (77 pass, exactly 2 named known failures).
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
If any deviation from baseline (new failure, missing pass, error other than the 2 known), **HALT** — debug before commit.

- [ ] **Step 5: Import smoke.**
```bash
~/frappe-bench/env/bin/python -c "from erpnext.controllers.subcontracting_controller import SubcontractingController; print('OK')"
```

- [ ] **Step 6: Commit.**
```bash
git add erpnext/controllers/subcontracting/__init__.py erpnext/controllers/subcontracting/validation.py erpnext/controllers/subcontracting_controller.py
git commit -m "refactor(subcontracting): extract validation cluster to subcontracting/validation.py"
```

### Task 2: Extract data_assembly cluster + 4 stays-method body rewrites (§3.6 rows 1, 4, 5, 6)

**Files:**
- Create: `erpnext/controllers/subcontracting/data_assembly.py` (~270 L)
- Modify: `erpnext/controllers/subcontracting_controller.py` — move 12 method bodies; add `from .subcontracting import data_assembly`; thin delegators; **rewrite 3 stays-method callsites + delete 1 dead-line**.

- [ ] **Step 1: Create `data_assembly.py` with 12 free functions.** All 12 take `doc` as first arg. Apply the name-mangling translation: every `__foo` from the controller becomes `_foo` in `data_assembly.py`. Methods (per spec §2 layout):
  - `_get_data_before_save(doc)` (was `__get_data_before_save`)
  - `_identify_change_in_item_table(doc)` (was `__identify_change_in_item_table`)
  - `_get_backflush_based_on(doc)` (was `__get_backflush_based_on`)
  - `initialized_fields(doc)` (public-named — no rename)
  - `_get_subcontract_orders(doc)` (was `__get_subcontract_orders`)
  - `_get_pending_qty_to_receive(doc)` (was `__get_pending_qty_to_receive`)
  - `_get_transferred_items(doc)` (was `__get_transferred_items`)
  - `_set_alternative_item_details(doc, row)` (was `__set_alternative_item_details`)
  - `_get_received_items(doc, doctype)` (was `__get_received_items`)
  - `_get_consumed_items(doc, doctype, receipt_items)` (was `__get_consumed_items`)
  - `_update_consumed_materials(doc, doctype, return_consumed_items=False)` (was `__update_consumed_materials`)
  - `get_available_materials(doc)` (public-named — no rename)

Mechanical substitutions in each moved body:
1. `self.X` → `doc.X` (general attribute / method access — for non-double-underscore identifiers).
2. **State attributes — `self.__attr` → `doc._attr`** (rename to single underscore so the name is literal in both module scope and class scope). Applies to: `__changed_name`, `__reference_name`, `__transferred_items`. Both writes (`doc.__changed_name = []` → `doc._changed_name = []`) and reads inside `data_assembly.py` use the renamed `_attr` form. The rename must match supplied_items.py's reads (see T3 Step 1 rule 2). **Without this rule:** `data_assembly.py`'s module-context write sets the literal attribute name `__changed_name`, but `SuppliedItemsHelper`'s class-context read compile-mangles to `_SuppliedItemsHelper__changed_name` → AttributeError at T3's behavior gate.
3. `self.__foo()` (method call, where `__foo` is in the moving list above):
   - If the callsite is **outside** `data_assembly.py` (e.g., in the controller or in another helper module): rewrite to `data_assembly._foo(doc)`.
   - If the callsite is **inside** `data_assembly.py` (helper-to-helper within the same module): rewrite to `_foo(doc, ...)` — direct module-local function call. Do NOT use `data_assembly._foo(...)` (self-import) or `doc._foo()` (would AttributeError — the controller no longer has the method).
4. `self.subcontract_data.X` → `doc.subcontract_data.X` (these stay as attribute access; no method-call rewrite needed)

**Preserve the 2 in-method local imports** at L458 and L467 (`from erpnext.deprecation_dumpster import deprecation_warning`) verbatim inside `_update_consumed_materials`.

- [ ] **Step 2: In `subcontracting_controller.py`, add the import and the 12 thin delegators / direct calls.**
```python
from erpnext.controllers.subcontracting import data_assembly
```
For the 2 public-named methods (`initialized_fields`, `get_available_materials`), add thin delegators on the controller:
```python
def initialized_fields(self):
    data_assembly.initialized_fields(self)

def get_available_materials(self):
    data_assembly.get_available_materials(self)
```
For the 10 `__`-prefixed methods, the controller no longer needs delegators (since `self.__foo` callsites are rewritten inline at the callsite — see Step 3). **Delete the 10 method defs from the controller.**

- [ ] **Step 3: Rewrite 3 stays-method bodies + delete 1 dead-line, per spec §3.6 rows 1, 4, 5, 6.**

a) `set_materials_for_subcontracted_items` at L1104 — rewrite L1109 only (L1110/L1111 are T3's job):
```python
def set_materials_for_subcontracted_items(self, raw_material_table):
    if self.doctype == "Purchase Invoice" and not self.update_stock:
        return

    self.raw_material_table = raw_material_table
    data_assembly._identify_change_in_item_table(self)             # was: self.__identify_change_in_item_table()
    self.__prepare_supplied_or_received_items()                     # T3 will rewrite this
    self.__validate_supplied_or_received_items()                    # T3 will rewrite this
```

b) `set_consumed_qty_in_subcontract_order` at L1138 — rewrite L1143 and L1151:
```python
def set_consumed_qty_in_subcontract_order(self):
    if self.doctype in ["Subcontracting Order", "Subcontracting Receipt"] or self.get(
        "is_old_subcontracting_flow"
    ):
        data_assembly._get_subcontract_orders(self)                 # was: self.__get_subcontract_orders()
        itemwise_consumed_qty = defaultdict(float)
        if self.get("is_old_subcontracting_flow"):
            doctypes = ["Purchase Receipt", "Purchase Invoice"]
        else:
            doctypes = ["Subcontracting Receipt"]

        for doctype in doctypes:
            consumed_items, receipt_items = data_assembly._update_consumed_materials(
                self, doctype, return_consumed_items=True
            )                                                        # was: self.__update_consumed_materials(doctype, return_consumed_items=True)

            for row in consumed_items:
                key = (row.rm_item_code, row.main_item_code, receipt_items.get(row.reference_name))
                itemwise_consumed_qty[key] += row.consumed_qty

        self.__update_consumed_qty_in_subcontract_order(itemwise_consumed_qty)
        # NOTE: __update_consumed_qty_in_subcontract_order STAYS on the controller (def at L1120).
        # Both ends of this callsite share the class scope, so Python name-mangling resolves
        # correctly. DO NOT rewrite this line.
```

c) `set_subcontracting_order_status` at L1271 — delete the dead-line at L1275:
```python
def set_subcontracting_order_status(self, update_bin=True):
    if self.doctype == "Subcontracting Order":
        self.update_status()
    elif self.doctype == "Subcontracting Receipt":
        # DELETED: self.__get_subcontract_orders   (no parens — pre-existing dead-line bug,
        # currently a no-op method-reference; would AttributeError after extraction).
        # self.subcontract_orders is populated by correct calls elsewhere (e.g., L1055, L1143).

        if self.subcontract_orders:
            for sco in set(self.subcontract_orders):
                sco_doc = frappe.get_doc("Subcontracting Order", sco)
                sco_doc.update_status(update_bin=update_bin)
```

- [ ] **Step 4: Behavior gate — run the 4-suite test block.** Must match baseline.

- [ ] **Step 5: Import smoke.**
```bash
~/frappe-bench/env/bin/python -c "from erpnext.controllers.subcontracting_controller import SubcontractingController; print('OK')"
```

- [ ] **Step 6: Commit.**
```bash
git add erpnext/controllers/subcontracting/data_assembly.py erpnext/controllers/subcontracting_controller.py
git commit -m "refactor(subcontracting): extract data_assembly cluster"
```

### Task 3: Extract `SuppliedItemsHelper` class + 2 stays-method body rewrites (§3.6 rows 2, 3)

**Files:**
- Create: `erpnext/controllers/subcontracting/supplied_items.py` (~545 L)
- Modify: `erpnext/controllers/subcontracting_controller.py` — add `SuppliedItemsHelper(self)` to `__init__`; move 25 method bodies; thin delegators for public-named methods; **rewrite 2 stays-method callsites**.

- [ ] **Step 1: Create `supplied_items.py` with `class SuppliedItemsHelper`.** Shape:

```python
# erpnext/controllers/subcontracting/supplied_items.py
import copy
import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, get_link_to_form

from erpnext.stock.doctype.batch.batch import get_batch_qty
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
    combine_datetime,
    get_auto_batch_nos,
    get_available_serial_nos,
    get_voucher_wise_serial_batch_from_bundle,
)
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.serial_batch_bundle import SerialBatchCreation, get_serial_nos_from_bundle
from erpnext.stock.utils import get_incoming_rate


class SuppliedItemsHelper:
    def __init__(self, controller):
        self.controller = controller

    def set_valuation_rate_for_rm(self):
        # body from controller L79-110, with `self.X` (where X is on the controller)
        # rewritten to `self.controller.X`. The local `rate_changed` flag and the
        # internal `kwargs = frappe._dict({...})` construction stay as-is.
        ...

    def _remove_changed_rows(self):
        ...

    def _remove_serial_and_batch_bundle(self, item):
        ...

    def _get_materials_from_bom(self, item_code, bom_no, exploded_item=0):
        ...

    def _update_reserve_warehouse(self, row, item):
        ...

    def _set_alternative_item(self, bom_item):
        ...

    def _set_serial_and_batch_bundle(self, item_row, rm_obj, qty):
        ...

    def _get_batch_nos_for_bundle(self, qty, key):
        ...

    def _get_serial_nos_for_bundle(self, qty, key):
        ...

    def _add_supplied_or_received_item(self, item_row, bom_item, qty):
        ...

    def set_batch_for_supplied_items(self):
        # IMPORTANT: preserve the 2 in-method local imports from L750-751:
        from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos_for_outward
        from erpnext.stock.get_item_details import get_filtered_serial_nos
        # ... rest of body verbatim, with `self.X` → `self.controller.X` where appropriate ...

    def batch_has_not_available(self, batch_no, qty_required):
        ...

    def update_rate_for_supplied_items(self):
        ...

    def get_item_row(self, reference_name):
        ...

    def set_rate_for_supplied_items(self, rm_obj, item_row):
        ...

    def _set_batch_nos(self, bom_item, item_row, rm_obj, qty):
        ...

    def _set_consumed_qty(self, rm_obj, consumed_qty, required_qty=0):
        ...

    def _set_serial_nos(self, item_row, rm_obj):
        ...

    def _set_batch_no_as_per_qty(self, item_row, rm_obj, batch_no, qty):
        ...

    def _get_qty_based_on_material_transfer(self, item_row, transfer_item):
        ...

    def _set_supplied_or_received_items(self):
        ...

    def _set_rate_for_serial_and_batch_bundle(self):
        ...

    def _modify_serial_and_batch_bundle(self):
        ...

    def _get_bundle_to_modify(self, name):
        ...

    def _prepare_supplied_or_received_items(self):
        ...

    def _validate_batch_no(self, row, key):
        ...

    def _validate_serial_no(self, row, key):
        ...

    def _validate_supplied_or_received_items(self):
        ...
```

Mechanical substitutions in each moved body:
1. `self.__foo(...)` (when `__foo` is also in this helper class) → `self._foo(...)` (the mangling rename within the helper).
2. **State attributes set by data_assembly — `self.__attr` (referring to a controller attribute) → `self.controller._attr`** (using the renamed single-underscore form established in T2 Step 1 rule 2). Applies to: `__changed_name`, `__reference_name`, `__transferred_items`. **Without this rule:** `self.controller.__changed_name` inside `SuppliedItemsHelper` compile-mangles to `self.controller._SuppliedItemsHelper__changed_name`, which doesn't exist on the controller instance (data_assembly's write used the literal name `__changed_name` and — per T2 Step 1 rule 2 — must be renamed to `_changed_name`) → AttributeError at T3's behavior gate.
3. `self.X` where `X` is an attribute on the controller (`self.supplied_items`, `self.subcontract_data`, `self.doctype`, `self.is_return`, `self.posting_date`, etc.) → `self.controller.X`.
4. `self.X()` where `X()` is a method that ALSO moved to this helper → stays `self.X()`.
5. `self.X()` where `X()` is a method on data_assembly.py → `data_assembly._X(self.controller)` (need `from . import data_assembly` at top of file).
6. `self.X()` where `X()` STAYS on the controller (e.g., `self.calculate_items_qty_and_amount()` called from `set_valuation_rate_for_rm`) → `self.controller.X()`.

The 25 methods listed above are all the methods moving to this cluster. Use the spec §2 layout as the authoritative list. **Delete each moved method from the controller** as it's added to the helper.

- [ ] **Step 2: Wire the helper into the controller's `__init__`.** Append after the existing init body (after the `else` branch that sets `self.subcontract_data`):
```python
# Existing import block at top of subcontracting_controller.py — add:
from erpnext.controllers.subcontracting.supplied_items import SuppliedItemsHelper

# In __init__ (current L27-56), after the existing if/elif/else that sets self.subcontract_data,
# append as a new statement:
class SubcontractingController(StockController):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.get("is_old_subcontracting_flow"):
            self.subcontract_data = frappe._dict(...)
        elif self.doctype == "Subcontracting Inward Order":
            self.subcontract_data = frappe._dict(...)
        else:
            self.subcontract_data = frappe._dict(...)
        self.supplied_items_helper = SuppliedItemsHelper(self)   # NEW
```

- [ ] **Step 3: Add thin delegators on the controller for public-named helper methods.** The public-named methods that need delegators (so existing controller-callers and subclass-dispatch keep working) are:
  - `set_valuation_rate_for_rm()` — called at L75 (controller's own `validate`)
  - `set_batch_for_supplied_items()` — called at L747
  - `update_rate_for_supplied_items()` — called at L746
  - `get_item_row(reference_name)` — survey callsites; add delegator if any external caller exists
  - `set_rate_for_supplied_items(rm_obj, item_row)` — called at L744
  - `batch_has_not_available(batch_no, qty_required)` — survey callsites; add delegator if needed

For each: replace the deleted method with:
```python
def set_valuation_rate_for_rm(self):
    self.supplied_items_helper.set_valuation_rate_for_rm()
# ... and similarly for the other 5 public-named methods ...
```

The 19 `_`-prefixed helper methods do NOT need controller delegators — they were only called from within the helper cluster (verified by V8) or from stays-on-controller methods that get rewritten in Step 4.

- [ ] **Step 4: Rewrite 2 stays-method body callsites per spec §3.6 rows 2, 3.** In `set_materials_for_subcontracted_items` at L1104 (already partially rewritten in T2), now rewrite L1110 and L1111:
```python
def set_materials_for_subcontracted_items(self, raw_material_table):
    if self.doctype == "Purchase Invoice" and not self.update_stock:
        return

    self.raw_material_table = raw_material_table
    data_assembly._identify_change_in_item_table(self)                      # rewritten in T2
    self.supplied_items_helper._prepare_supplied_or_received_items()         # was: self.__prepare_supplied_or_received_items()
    self.supplied_items_helper._validate_supplied_or_received_items()        # was: self.__validate_supplied_or_received_items()
```

- [ ] **Step 5: Behavior gate — run the 4-suite test block.** Must match baseline.

- [ ] **Step 6: Import smoke.**
```bash
~/frappe-bench/env/bin/python -c "from erpnext.controllers.subcontracting_controller import SubcontractingController; print('OK')"
```

- [ ] **Step 7: Commit.**
```bash
git add erpnext/controllers/subcontracting/supplied_items.py erpnext/controllers/subcontracting_controller.py
git commit -m "refactor(subcontracting): extract SuppliedItemsHelper class"
```

### Task 4: Extract module-level API + whitelist re-export + manual smoke

**Files:**
- Create: `erpnext/controllers/subcontracting/api.py` (~230 L)
- Modify: `erpnext/controllers/subcontracting_controller.py` — move 6 module-level functions; add re-export block at bottom of file.
- Modify: `erpnext/controllers/subcontracting/validation.py` — retarget `get_pending_subcontracted_quantity` import.

- [ ] **Step 1: Create `api.py`** with the 6 module-level functions moved verbatim from `subcontracting_controller.py` (def positions at L1356, L1372, L1383, L1503, L1523, L1571):
```python
# erpnext/controllers/subcontracting/api.py
import json

import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt
# ... any other imports the 6 functions use; copy from controller's import block ...


def get_item_details(items):
    # body verbatim from controller L1356-1370
    ...


def get_pending_subcontracted_quantity(doctype, name):
    # body verbatim from controller L1372-1379
    ...


@frappe.whitelist()
def make_rm_stock_entry(
    subcontract_order, rm_items=None, order_doctype="Subcontracting Order", target_doc=None
):
    # body verbatim from controller L1383-1501
    ...


def add_items_in_ste(ste_doc, row, qty, rm_details, rm_detail_field="sco_rm_detail", batch_no=None):
    # body verbatim from controller L1503-1521
    ...


def make_return_stock_entry_for_subcontract(...):
    # body verbatim from controller L1523-1567
    ...


@frappe.whitelist()
def get_materials_from_supplier(subcontract_order, rm_details, order_doctype="Subcontracting Order"):
    # body verbatim from controller L1571-1585
    ...
```

The `@frappe.whitelist()` decorators on `make_rm_stock_entry` and `get_materials_from_supplier` are copied verbatim. No re-decoration needed.

- [ ] **Step 2: Delete the 6 function definitions from `subcontracting_controller.py`** (L1356-1585 range). At the **bottom of the file** (after the last remaining definition), add the re-export block:
```python
# Re-export whitelisted + helper functions from .subcontracting.api to preserve the
# original public API paths:
#   /api/method/erpnext.controllers.subcontracting_controller.make_rm_stock_entry
#   /api/method/erpnext.controllers.subcontracting_controller.get_materials_from_supplier
# JS callsites (purchase_order.js L109/L483, subcontracting_order.js L596/L704) and
# Python importers (stock_entry.py:4170, 6 test files) keep working without changes.
# Frappe's URL resolution (frappe.get_attr → getattr) uses module-attribute lookup, and
# @frappe.whitelist() decoration travels with the function object — no re-decoration needed.
from erpnext.controllers.subcontracting.api import (  # noqa: F401
    add_items_in_ste,
    get_item_details,
    get_materials_from_supplier,
    get_pending_subcontracted_quantity,
    make_return_stock_entry_for_subcontract,
    make_rm_stock_entry,
)
```

- [ ] **Step 3: Retarget `validation.py`'s import** (set up in T1):
```python
# erpnext/controllers/subcontracting/validation.py — change:
# BEFORE (T1):
from erpnext.controllers.subcontracting_controller import get_pending_subcontracted_quantity
# AFTER (T4):
from .api import get_pending_subcontracted_quantity
```

- [ ] **Step 4: Behavior gate — run the 4-suite test block.** Must match baseline.

- [ ] **Step 5: Import smoke.**
```bash
~/frappe-bench/env/bin/python -c "from erpnext.controllers.subcontracting_controller import SubcontractingController, make_rm_stock_entry, get_materials_from_supplier; print('OK')"
```

- [ ] **Step 6: Whitelist-URL manual smoke.** Confirm the URL resolution path still works after re-export.
```bash
cd ~/frappe-bench
~/.local/bin/bench --site test_site console <<'EOF'
import frappe
fn = frappe.get_attr("erpnext.controllers.subcontracting_controller.make_rm_stock_entry")
print("get_attr OK:", fn.__name__, "from", fn.__module__)
print("is_whitelisted:", frappe.is_whitelisted(fn))
fn2 = frappe.get_attr("erpnext.controllers.subcontracting_controller.get_materials_from_supplier")
print("get_attr OK:", fn2.__name__, "from", fn2.__module__)
print("is_whitelisted:", frappe.is_whitelisted(fn2))
EOF
```
Expected:
- `get_attr OK: make_rm_stock_entry from erpnext.controllers.subcontracting.api`
- `is_whitelisted: True`
- (and equivalent for `get_materials_from_supplier`)

If either `get_attr` raises `AttributeError` or `is_whitelisted` returns `False` → **HALT** — re-export pattern is broken; investigate before commit.

- [ ] **Step 7: Commit.**
```bash
git add erpnext/controllers/subcontracting/api.py erpnext/controllers/subcontracting/validation.py erpnext/controllers/subcontracting_controller.py
git commit -m "refactor(subcontracting): extract module-level API + whitelist re-export"
```

### Task 5: Measure (AFTER + report)

**Files:**
- Create: `docs/measurements/03-subcontracting-controller-god-class-split.md`

- [ ] **Step 1: Capture AFTER perf** (tree is now at T4's HEAD with full extraction landed).
```bash
cd ~/frappe-bench
env/bin/python ~/erpnext/docs/measurements/scripts/measure_part3_refactor.py \
    --site test_site --output /tmp/measure_part3_after.json
```

- [ ] **Step 2: Capture AFTER structural metrics.**
```bash
cd ~/erpnext
echo "--- controller ---"
wc -l erpnext/controllers/subcontracting_controller.py
echo "--- helpers ---"
wc -l erpnext/controllers/subcontracting/*.py
echo "--- sum ---"
wc -l erpnext/controllers/subcontracting_controller.py erpnext/controllers/subcontracting/*.py | tail -1
echo "--- methods per class (controller) ---"
grep -cE "^\s*def " erpnext/controllers/subcontracting_controller.py
echo "--- methods per class (SuppliedItemsHelper) ---"
grep -cE "^\s*def " erpnext/controllers/subcontracting/supplied_items.py
echo "--- module-level functions per file ---"
for F in erpnext/controllers/subcontracting_controller.py erpnext/controllers/subcontracting/*.py; do
  echo -n "$F: "; grep -cE "^def " "$F"
done
echo "--- radon CC ---"
~/frappe-bench/env/bin/radon cc -s -a erpnext/controllers/subcontracting_controller.py erpnext/controllers/subcontracting/*.py
echo "--- radon MI ---"
~/frappe-bench/env/bin/radon mi erpnext/controllers/subcontracting_controller.py erpnext/controllers/subcontracting/*.py
echo "--- import-graph fan-out ---"
grep -rln "erpnext.controllers.subcontracting_controller" --include="*.py" --include="*.js" --include="*.json" erpnext/ | wc -l
```

- [ ] **Step 3: Write `docs/measurements/03-subcontracting-controller-god-class-split.md`.** Sections:
  1. **Methodology** — distilled from spec §4.2 + the cold/warm-cache discipline note.
  2. **Structural metrics table** — BEFORE vs AFTER for: LOC per file, methods per class, module-level functions per file, cyclomatic complexity (per-file aggregate + sum), maintainability index per file, import-graph fan-out. Numbers from the baseline stub and Step 2 outputs.
  3. **Perf parity table** — per (subject, N) tuple: cold queries BEFORE/AFTER (binary equality check), warm queries BEFORE/AFTER, cold latency_ms median BEFORE/AFTER (with ±10% tolerance verdict at N=20), warm latency_ms median BEFORE/AFTER (same tolerance). Source data joined from `/tmp/measure_part3_before.json` and `/tmp/measure_part3_after.json`.
  4. **Parity verdict** — pass/fail per spec §4.2's criteria. If query count drifted on ANY (subject, N) → fail and HALT before T6. If cold or warm latency median drifted >10% at N=20 → fail and HALT; investigate.
  5. **Notes on noise / variance** — if any (subject, N)'s 5-run spread exceeds 20% at N=1 or 10% at N=20, flag it (consistent with Part 2's observed noise floor).
  6. **References** — link to spec, plan, baseline stub, lesson (T6).

- [ ] **Step 4: Commit.**
```bash
git add docs/measurements/03-subcontracting-controller-god-class-split.md
git commit -m "docs: measure SubcontractingController god-class split before/after"
```

### Task 6: Lesson (Part 3, complex)

**Files:**
- Create: `docs/lessons/03-complex-subcontracting-god-class-split.md` (~400-450 L target)

- [ ] **Step 1: Write the lesson** mirroring `docs/lessons/02-intermediate-sales-invoice-n1s.md`'s shape:
  - **Title:** "Lesson 3: SubcontractingController God-Class Split (Complex)"
  - **Commits / refs** — list T0 through T5 commit SHAs (resolved at write time), source spec (`e7220d7a19`), plan, baseline stub, measurement report, lesson itself.
  - **What we found** — the 1585-line god class, the 54 methods, the 4 distinct concern clusters identified by reading the file.
  - **How we identified it** — arch review Finding 1.10 + the cluster identification process (grep-by-prefix, mutator-vs-validator distinction, whitelist-URL surfacing).
  - **What we did** — for each cluster, before/after code snippet showing the pattern:
    1. Validation (free functions) — smallest, simplest.
    2. Data assembly (free functions with cross-module helper call) — shows scale.
    3. SuppliedItemsHelper (helper class with back-reference) — shows the pattern shift.
    4. Module-level API + re-export (whitelist URL preservation) — done last for rollback isolation.
  - **What we learned about name mangling** — the §3.6 stays-method body rewrites: why Python's `__`-prefix mangling means moving a method silently breaks every callsite to it that stays on the class.
  - **What we measured** — cite the actual structural numbers from `docs/measurements/03-*.md` (LOC, CC, MI per file) and perf parity verdict (queries identical, latency within ±10% on N=20 cold + warm).
  - **What we learned** — 4 numbered pedagogical takeaways, one per API-preservation pattern. Plus a 5th on the gotcha of stays-method body rewrites (the lesson's most counterintuitive finding).
  - **Verification stack** — behavior gate (77/79 baseline), structural metrics, perf parity, manual whitelist-URL smoke.
  - **Transferable patterns** — when to use free functions vs helper class vs re-export; the migration ordering principle (riskiest last for rollback isolation).
  - **What this lesson is / isn't** — explicit about what wasn't proved (no MI→bug-rate causation, no human-comprehension claims, no perf improvement claim).
  - **Cross-refs** — Part 1 (warm-up), Part 2 (intermediate), arch review.
  - **Next steps** — the items from spec §6 (Out of scope), framed as future work.

- [ ] **Step 2: Commit.**
```bash
git add docs/lessons/03-complex-subcontracting-god-class-split.md
git commit -m "docs: add complex lesson — SubcontractingController god-class split"
```

## Tasks NOT in this plan

(Inherited verbatim from spec §6 — preserved as a table because the spec used a table.)

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
| **Fix the 2 baseline test failures** (`test_secondary_items_delivery`, `test_work_order_creation_qty`) | Pre-existing fixture-data issue. Out of scope per user decision (spec §4.4). | Investigate Subcontracting BOM autoname / seed `SB-0001` fixture as a separate one-off cleanup. |
| Cyclomatic-complexity threshold enforcement | Reporting metrics is in scope; enforcing thresholds is not. | If desired, add radon to CI as a separate initiative. |

A new spec → new plan cycle is required to add any of these.

## Known issues inherited from spec

(Inherited verbatim from spec §4.4 and §4.5 — preserved as a mixed bullet/prose form matching the source.)

**From spec §4.4 — known baseline test failures, accepted as out of scope:**

The 2 baseline test failures in `test_subcontracting_inward_order` are accepted as pre-existing:

- `test_secondary_items_delivery` (line 333): `frappe.get_doc("Subcontracting BOM", "SB-0001")` raises `DoesNotExistError` — the fixture record `SB-0001` does not exist in `test_site`.
- `test_work_order_creation_qty` (line 161): same root cause.

Both are fixture-data issues, not code bugs. The refactor neither addresses nor worsens them. They are NOT in the refactor's touch surface. Fixing them is future work.

**From spec §4.5 — what this verification does NOT prove:**

Explicitly out of scope, called out so the lesson is honest:
- That the refactor improved *human* comprehension — metrics can't verify subjective claims.
- That maintainability metrics correlate with bug rates — they don't, conclusively. Radon numbers are descriptive, not causal.
- That perf is *better* — only that it's the same (parity gate, not improvement gate).
- That the 2 known-failing tests' underlying issues have been investigated.
