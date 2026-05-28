# Lesson 3: SubcontractingController God-Class Split (Complex)

This is the third and final part of a three-part refactoring series on the ERPNext codebase. The warm-up (Lesson 1) extracted a long braided method into single-concern helpers. The intermediate (Lesson 2) widened the surface to five N+1 sites across three methods and added empirical before/after measurement. This complex lesson scales again — from one method to **a 1585-line god class with 54 methods** — and shifts the dominant technique from "rewrite the loop body" to "preserve the public API while moving 80% of the code to four new modules." The new ingredients are **API preservation patterns** (free functions, helper class with back-reference, whitelist re-export) and **Python name-mangling translation**.

A note on the pivot from Lesson 02's "Next steps": the warm-up anticipated "a method or method-cluster with non-trivial dependencies" — this is that exercise, scaled up to a whole class. The verification pipeline (spec → CDR → plan → CIR → subagent execution + measurement) carried over without modification. The new discipline is **per-commit behavior-gate enforcement at four intermediate commits**, not just before and after.

**Commits:**
- T0 baseline (measurement script + baseline stub + BEFORE perf JSON): `8431114719`
- T1 validation cluster (4 free functions): `88a55033`
- T2 data_assembly cluster (12 free functions + 4 stays-method rewrites): `2b85c304a4`
- T2 cleanup (copyright header + remove dead imports): `928747922a`
- T3 SuppliedItemsHelper class (28 methods + 2 stays-method rewrites): `910231b8b8`
- T4 module-level API + whitelist re-export + manual smoke: `25efc1ead4`
- T5 measurement report: `583e2a7560`
- T5 re-measurement (20-run diagnostic for N=20 latency): `663fcd1009`
- Lesson: this commit
- Source spec: `docs/specs/2026-05-28-subcontracting-controller-god-class-split-design.md` (commit `e7220d7a19`)
- Implementation plan: `docs/plans/2026-05-28-subcontracting-controller-god-class-split-implementation-plan.md` (commit `01fdc26249`, fixes `f3157ac3fc`)
- Baseline stub: `docs/measurements/03-subcontracting-controller-god-class-split-baseline.md` (T0)
- Measurement report: `docs/measurements/03-subcontracting-controller-god-class-split.md` (T5 + re-measure)
- Architecture review: `docs/reviews/2026-05-27-erpnext-architecture-review-1.md`, Finding 1.10

---

## What we found

`erpnext/controllers/subcontracting_controller.py` — a 1585-line `SubcontractingController` class with 54 instance methods and 6 module-level functions. Three subclasses inherit from it (`SubcontractingOrder`, `SubcontractingReceipt`, `SubcontractingInwardOrder`); none override any of the 54 methods. Radon graded the file at **CC avg B (5.82)** and **MI C** — the only C-grade controller in the directory.

Reading the file revealed four distinct concern clusters with little internal coupling between them:

| Cluster | Method count | What it does | Mutates state? |
|---|---:|---|---|
| **Validation** | 4 | Pre-save checks on items, warehouses, conversion factors | No (raises only) |
| **Data assembly** | 12 | Read upstream Purchase/Sales Orders, build internal data dicts | Light (sets `self.subcontract_orders` etc.) |
| **Supplied items** | 28 | Compute supplied/received items, batch/serial allocation, rate setting | Heavy (writes to `self.supplied_items`, `self.received_items`, batch rows) |
| **Module-level API** | 6 | Whitelisted endpoints called from JS + REST clients | Builds Stock Entries / RM transfers |

Plus ~10 methods that stay on the controller (lifecycle hooks, orchestrators, stock-ledger code tightly coupled to the `StockController` base class, and two methods decorated with `@frappe.whitelist()` that Frappe's `run_doc_method` resolves through the class).

The whitelist URL surface was load-bearing: 4 JS callsites and 6+ Python importers reach the module through `erpnext.controllers.subcontracting_controller.<name>`. Any rename of the file or any module-level function would be a breaking API change.

---

## How we identified it

The candidate came from the same architecture review that fed the warm-up and intermediate lessons, this time from **Finding 1.10** (god-class candidates by LOC and method count). `subcontracting_controller.py` was the largest controller in the directory and the only one with `MI = C`.

Cluster identification was manual, not mechanical:

1. **Grep by prefix.** `grep -nE "^\s*def " subcontracting_controller.py | sort -k4` surfaced naming patterns — `validate_*`, `set_*`, `get_*`, `make_*`. Methods sharing a prefix tend to share a concern, but the prefix isn't reliable on its own (e.g., `set_valuation_rate_for_rm` looks like a setter but is actually a mutator-driven supplied-items concern).
2. **Mutator vs. validator distinction.** Each `validate_*`-prefixed method was re-read to confirm it only raises (no writes). One candidate (`set_valuation_rate_for_rm`) was initially mis-classified as validation; it writes `row.rate`, `row.amount` and calls `self.calculate_items_qty_and_amount()`. Re-classified to supplied_items (verified assumption V19 in the spec).
3. **Whitelist URL surfacing.** `grep -n "@frappe.whitelist" subcontracting_controller.py` surfaced 4 hits — 2 module-level (`make_rm_stock_entry`, `get_materials_from_supplier`) and 2 class-level (`get_current_stock`, `sub_contracted_items`). The 2 module-level functions and their 4 non-whitelisted module-level neighbors became the **api.py** cluster. The 2 class-level ones stay (Frappe resolves them through `run_doc_method`, keyed on the class).

The result: 4 clusters totaling 50 methods, 4 to stay on the controller as orchestrators/lifecycle, plus ~10 more to stay (stock-ledger code, calculators, properties).

**Pedagogical takeaway from cluster identification:** naming conventions are a starting hypothesis; read every body to confirm. The cost of a mis-classification (e.g., moving a mutator to a "validation" module) compounds across every callsite touched.

---

## What we did

Four extraction commits, one per cluster, ordered smallest-and-simplest first → highest-blast-radius last. Each pattern progressively builds on the previous one's scaffolding. Test gate (77/79, matching baseline exactly) was enforced at every intermediate commit; any deviation would have stopped the loop.

### Cluster 1 — Validation (T1): free functions, lowest risk

4 methods (`validate_rejected_warehouse`, `remove_empty_rows`, `set_items_conversion_factor`, `validate_items`) moved to `subcontracting/validation.py` as free functions taking `doc` as their first argument. Controller keeps thin delegators so subclass-dispatch behavior is unchanged.

Before:

```python
class SubcontractingController(StockController):
    def validate_rejected_warehouse(self):
        for item in self.get("items"):
            if flt(item.rejected_qty) and not item.rejected_warehouse:
                if self.rejected_warehouse:
                    item.rejected_warehouse = self.rejected_warehouse
                else:
                    frappe.throw(_("..."))
```

After (controller side):

```python
def validate_rejected_warehouse(self):
    validation.validate_rejected_warehouse(self)
```

After (`subcontracting/validation.py`):

```python
def validate_rejected_warehouse(doc):
    for item in doc.get("items"):
        if flt(item.rejected_qty) and not item.rejected_warehouse:
            ...
```

`self` becomes `doc`; nothing else changes. The validation cluster mutates no state, raises only, and has no `__`-prefixed members — the smallest cluster, chosen first to de-risk the test loop and prove the free-function pattern.

### Cluster 2 — Data assembly (T2): free functions with cross-module helper call + the name-mangling reckoning

12 methods (`_get_data_before_save`, `_identify_change_in_item_table`, `_get_backflush_based_on`, `initialized_fields`, `_get_subcontract_orders`, etc.) moved to `subcontracting/data_assembly.py` as free functions. Same shape as T1, except that this cluster had **9 `__`-prefixed members** (out of the original 30+ in the file). All 9 were renamed `__foo` → `_foo` (module-private convention) because Python name mangling only applies inside class bodies.

The translation rule for callsites inside the class:

```python
# Before (inside the controller class body)
self.__get_subcontract_orders()
# After
data_assembly._get_subcontract_orders(self)
```

What we did NOT anticipate: T2 also had to rewrite four **stays-method** callsites (spec §3.6 caught three; one more surfaced during execution). See the dedicated subsection below.

### Cluster 3 — SuppliedItemsHelper (T3): helper class with back-reference, the pattern shift

28 methods moved to `subcontracting/supplied_items.py` as instance methods on a new `SuppliedItemsHelper` class. The controller's `__init__` instantiates the helper with a back-reference (`subcontracting_controller.py:47`), and 7 public-named methods get thin delegators on the controller.

```python
# subcontracting_controller.py:47
self.supplied_items_helper = SuppliedItemsHelper(self)

# subcontracting_controller.py:71 (one of 7 delegators)
def set_valuation_rate_for_rm(self):
    self.supplied_items_helper.set_valuation_rate_for_rm()
```

The helper class:

```python
class SuppliedItemsHelper:
    def __init__(self, controller):
        self.controller = controller

    def set_valuation_rate_for_rm(self):
        if self.controller.doctype == "Subcontracting Receipt":
            for row in self.controller.supplied_items:
                ...
```

Every state access goes through `self.controller.<attr>`. The 28-method cluster has dense internal coupling (e.g., `set_batch_for_supplied_items` calls `batch_has_not_available`, which calls `_get_materials_from_bom`) — refactoring them as 28 free functions would have required passing 5+ shared dicts around explicitly. The class-with-back-reference pattern keeps the implicit shared state working while still isolating the cluster from the controller.

The class also carries the file's 3 highest-CC hotspots (`set_batch_for_supplied_items` C-19, `_set_supplied_or_received_items` C-19, `_set_batch_nos` C-14). These were moved verbatim — the spec scoped the refactor as **structural-only**, no internal refactoring of method bodies. `supplied_items.py` is consequently the sole AFTER file at MI grade C.

### Cluster 4 — Module-level API + whitelist re-export (T4): done last for rollback isolation

6 module-level functions moved to `subcontracting/api.py` with their `@frappe.whitelist()` decoration preserved verbatim on `make_rm_stock_entry` and `get_materials_from_supplier`. The controller's tail gets a re-export block:

```python
# subcontracting_controller.py:359-374
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

Frappe's URL resolver does `getattr(get_module(modulename), methodname)` — the re-export keeps the same function object accessible at the original path. Manually verified from `bench console` that both whitelisted functions resolve via `frappe.get_attr("erpnext.controllers.subcontracting_controller.make_rm_stock_entry")` and remain in `frappe.whitelisted` (set membership check).

T4 was done last specifically so it's an isolated rollback target: if the JS frontend or an external integration broke, `git revert 25efc1ead4` undoes only the API move and leaves T1-T3's 1199-LOC structural reduction intact.

### The fifth thing we did — Python name-mangling translation (the counterintuitive finding)

Python's `__`-prefix rule: any identifier of the form `__name` (no trailing `__`) inside a class body is silently rewritten by the compiler to `_<ClassName>__name`. **This rewrite happens at compile time, lexically scoped to the enclosing class.** Module-level functions don't mangle. Cross-module references don't mangle. References from inside a different class mangle to *that* class's name.

Implications for the refactor:

| Site | What looks like | What it actually is | After extraction |
|---|---|---|---|
| `def __foo(self)` inside the controller class | A "private" method named `__foo` | An attribute named `_SubcontractingController__foo` | Rename to `_foo` (single underscore = convention "module-private", no mangling) |
| `self.__foo()` inside any method of the controller | A call to `self._SubcontractingController__foo()` | Mangled lookup at compile time | After moving `__foo` out of the class: lookup misses → `AttributeError` |
| `self.controller.__bar` inside `SuppliedItemsHelper` body | Looks like access to the controller's `__bar` | Mangled to `self.controller._SuppliedItemsHelper__bar` (wrong class!) | Never resolves the original `_SubcontractingController__bar` |

**The spec (§3.6) caught one variant of this hazard:** methods that **stay on the controller** but call `__`-prefixed methods that **move** to helpers. Without rewrites, those stays-method callsites would resolve via the old mangled name to nothing. The spec enumerated 6 known stays-method callsite rewrites split across T2 and T3.

**The plan missed a second variant:** during T2 execution, three additional callsites surfaced — `supplied_items.py` methods (still on the controller at T2 time, before T3 moved them) reading data_assembly state attributes that had just been renamed. The original lookups went `self.__some_state_attr` (mangled `_SubcontractingController__some_state_attr`); after T2's `__` → `_` rename the state attribute was `_some_state_attr`, but the readers (still in the controller class body) were still mangling. The fix: rename the state attributes in T2's commit and rewrite the readers in supplied-items methods at the same time. Three unenumerated callsite rewrites and three state-attribute renames landed in T2 to keep the test gate green at the T2 boundary.

The lesson here is the most counterintuitive in the series: **the unit of name-mangling is the class body, not the file.** A method moving out of a class breaks every callsite to it that *stays* in the class. The reverse also applies — a method staying in the class breaks reads of its `__`-prefixed state from any callsite that moved out.

---

## Test coverage decisions

No new tests written. The 77-pass behavior gate uses the existing 4 test modules (controller + 3 subclass test suites) at exactly the 77/79 baseline (2 known fixture failures in `test_subcontracting_inward_order` are pre-existing, out of scope per spec §4.4). This gate was enforced at **every** of the 6 code commits (T1, T2, T2-cleanup, T3, T4) — not just before-and-after.

This matches Lesson 02's approach: rely on the existing suite's transitive coverage of the refactored surface, add tests only when a coverage gap is identified. No gap was identified; all 4 clusters had submit-path coverage that exercised the extracted methods through the lifecycle hooks.

---

## What we measured

The structural and perf numbers below are from `docs/measurements/03-subcontracting-controller-god-class-split.md`. BEFORE captured at T0 (`8431114719`); AFTER captured at T5 with N=20 cells re-measured at `663fcd1009`.

### Structural

| Metric | BEFORE | AFTER | Delta |
|---|---:|---:|---|
| Controller LOC | 1585 | 374 | **−1211 (−76%)** |
| Helpers LOC (sum of `subcontracting/*.py`) | 0 | 1325 | +1325 |
| Total LOC (controller + helpers) | 1585 | 1699 | **+114 (+7%)** |
| Controller methods | 68 | 29 | −39 (−57%) |
| Controller module-level functions | 6 | 0 | −6 (−100%) |
| Radon CC avg (controller) | B (5.82) | A (4.96) | improved |
| Radon MI (controller) | C | A | improved |
| Import-graph fan-out | 13 | 14 | +1 (new `api.py` consumer; original public path preserved by re-export) |

Per-file AFTER:

| File | LOC | Methods/Functions | CC avg | MI |
|---|---:|---:|---:|---:|
| `subcontracting_controller.py` | 374 | 29 / 0 | A (4.5) | A |
| `subcontracting/validation.py` | 135 | 0 / 4 | B (8.2) | A |
| `subcontracting/data_assembly.py` | 340 | 0 / 12 | B (7.5) | A |
| `subcontracting/supplied_items.py` | 609 | 29 / 0 | B (8.0) | C |
| `subcontracting/api.py` | 241 | 0 / 6 | B (5.0) | A |

The +7% total-LOC growth is honest accounting: 4 new copyright headers (8 lines), the `SuppliedItemsHelper.__init__` + back-reference plumbing, `self.controller.` prefixes on every state access in the helper class, and ~12 thin delegators on the controller. The price of API preservation.

### Perf parity

| Subject | N | Cache | Q Δ | Lat Δ ms | Lat Δ % | Verdict |
|---|---:|---|---:|---:|---:|---|
| SCO  | 20 | cold | 0 | +0.2 | +0.2% | PASS |
| SCO  | 20 | warm | 0 | +1.1 | +1.0% | PASS |
| SCR  | 20 | cold | 0 | +0.5 | +0.6% | PASS |
| SCR  | 20 | warm | 0 | −0.3 | −0.3% | PASS |
| SCIO | 20 | cold | 0 | +0.6 | +1.2% | PASS |
| SCIO | 20 | warm | 0 | +1.7 | +3.5% | PASS |

(SCO = SubcontractingOrder.validate, SCR = SubcontractingReceipt, SCIO = SubcontractingInwardOrder.)

**Query count:** AFTER == BEFORE exactly for all 18 cells (3 subjects × 3 N × cold/warm). No `frappe.db.*` calls added or removed by the extraction.
**Latency at N=20:** All 6 cells within ±10%, max drift 3.5% (SCIO warm). Verdict **PASS**.
**Latency at N=1, N=5:** Informational only (small-N variance is 20-50%, dominated by µs-level framework overhead). Not gated per spec §4.2.

### The re-measurement story

The initial 5-run AFTER measurement flagged SCR warm N=20 at +12.0% drift (BEFORE 84.77ms → AFTER 94.92ms) — the sole ±10% tolerance violation. The BEFORE variance was suspiciously tight (1.7% vs Part 2's typical 3-10% floor at N=20). Re-running BOTH ends with 20 iterations (instead of 5) at the same commits, same workload, same cache state:

| Metric | BEFORE (5 runs) | BEFORE (20 runs) | AFTER (5 runs) | AFTER (20 runs) |
|---|---:|---:|---:|---:|
| SCR warm N=20 median | 84.77ms | **88.34ms** | 94.92ms | **88.04ms** |
| SCR warm N=20 variance | 1.7% | 14.9% | 10.2% | 14.1% |

True delta: −0.3% (statistical noise). The original +12.0% was a sampling artifact — the 5-sample BEFORE caught a low-side cluster (1.7% variance is atypically tight); the 5-sample AFTER caught a high-side cluster. With 20 samples, both medians converged to ~88ms with normal ~14% variance.

This becomes Takeaway 5 below.

---

## What we learned

Five pedagogical takeaways. Four correspond to the four API-preservation patterns; the fifth is the measurement gotcha.

### (a) Free-function delegation is the lowest-friction extraction shape — use it first

When a cluster's methods don't share state with each other beyond what's already on `self`, extract them as module-level free functions taking the host object as the first argument. Replace the class methods with one-line delegators (`def x(self): module.x(self)`). Subclass dispatch is preserved (delegators are still class methods). The pattern adds 1 line of delegator per extracted method — cheap.

This was T1's shape (4 methods → 4 free functions) and T2's shape (12 methods → 12 free functions). The whole `validation.py` and `data_assembly.py` clusters use it. The pattern works for any extracted method that needs read-mostly access to `self` and has no need to share state with a sibling extracted method beyond what `self` already exposes.

**When it doesn't work:** when the cluster has dense internal coupling. Then T3's pattern.

### (b) Helper class with back-reference is the right shape for tightly-coupled clusters

When the cluster has dense internal coupling — methods calling sibling methods, sharing computed dicts, threading shared state through 5+ calls — promote it to a helper class with a `controller` back-reference. Controller instantiates it once in `__init__`; helper methods reach controller state via `self.controller.<attr>`. Public-named methods (those the controller's external API exposed) get thin delegators on the controller.

T3 used this for the 28-method supplied_items cluster. The alternative (28 free functions threading shared dicts through 5+ explicit parameters) would have been mechanically possible but would have grown the call signatures without limit. The back-reference is the cheapest way to preserve implicit shared state.

**Cost:** every state access becomes `self.controller.<attr>` (longer than `self.<attr>`). The +7% total LOC in this refactor's accounting is mostly this.

### (c) Module-level re-export preserves whitelist URLs and external import paths — and isolates the riskiest extract for rollback

Whitelisted functions are reached via `/api/method/<modulepath>.<name>`, which Frappe resolves via `frappe.get_attr → getattr(module, name)`. If you move the function to a different module and re-export it from the original (`from .new_module import (foo, bar)` at the bottom of the original file), the lookup still works. JS callsites don't change. Python importers don't change. The `@frappe.whitelist()` decoration travels with the function object — no re-decoration needed.

T4 used this for the 6 module-level functions, 2 of which were whitelisted. Done last so that the highest-blast-radius extract has the cleanest rollback. If the JS frontend had broken in production after T4, `git revert 25efc1ead4` would have undone only the API move and left T1-T3's 76% controller-LOC reduction intact.

### (d) Python name mangling is a refactoring hazard wherever a `__`-method moves across a class boundary

`self.__foo()` inside class A compiles to `self._A__foo()`. When `__foo` moves out of class A:
- Every callsite to `__foo` inside A (including ones that *stay* in A) breaks.
- Every callsite that READS state attributes named `__bar` from inside a *different* class (e.g., from a helper class with a back-reference) mangles to the wrong name.

The rename `__foo` → `_foo` (single underscore = module-private convention, no mangling) is the standard fix. **Every callsite to the renamed name must be rewritten — including ones in methods that stay on the original class.** The spec enumerated 6 such rewrites for this refactor; three more surfaced during execution because of cross-class state access. The mismatch between predicted rewrites and required rewrites is the lesson: a structural refactor that touches `__`-prefixed names is not enumerable from grep alone.

### (e) 5-sample N=20 latency means tight noise can lie; bump to 20+ samples for marginal cases

The T5 initial measurement flagged a false +12% latency regression because a 5-sample BEFORE distribution had 1.7% variance (atypically tight — the true population variance at N=20 on this bench is ~10-15%). The tight variance produced a low-side median, and the 5-sample AFTER caught a few high-side outliers. With 20 samples at the same commits, both ends converged to ~88ms with normal ~14% variance and true delta −0.3%.

The protocol fix is simple: **when a marginal cell barely exceeds the tolerance threshold (10-15% drift) or when sample variance is atypically tight (<3% at N=20), increase sample count before declaring a regression.** The 5-sample protocol is adequate for detecting large drifts (>20%); marginal cases need higher-N sampling to separate signal from noise.

This is the same shape of issue as Lesson 02's cache-discipline gotcha (a measurement-script artifact that silently produces plausible-but-wrong numbers). It is worth a self-check whenever a single cell in a parity table looks isolated or surprising.

---

## Verification stack used

Same pipeline as the warm-up and intermediate, with one new discipline:

1. **Spec** (`thorough-brainstorming`) — empirically verified design. 22 verified assumptions recorded.
2. **Critical Design Review (CDR), 1 round** — adversarial review of the spec. Gitignored. 1 fix applied.
3. **Plan** (`thorough-writing-plans`) — task-level plan with every file path, method signature, line-number callsite, and shell command verified against the real codebase.
4. **Critical Implementation Review (CIR), 1 round** — adversarial review of the plan. Gitignored. 2 fixes applied (the `f3157ac3fc` commit).
5. **Subagent-driven execution** — 7 tasks (T0 baseline through T6 lesson), each in its own subagent with explicit scope and self-review. Behavior gate ran green at every code commit (T1 through T4).
6. **Empirical measurement** (cold + warm paths, 3 subjects × 3 N values) — re-runnable script, before/after capture, JSON output, written report. Followed by 20-run re-measurement when an N=20 cell flagged a marginal drift.
7. **Manual smoke** for the whitelisted functions: `bench console` + `frappe.get_attr` + `frappe.whitelisted` set membership check, before and after the re-export, to confirm URL resolution and whitelist registration are preserved.

The new discipline relative to Lesson 02: **per-commit behavior-gate enforcement at every intermediate commit, not just before-and-after.** The 4-step extraction means 4 intermediate commits, each of which must independently satisfy the 77-pass gate. Any deviation stops the SDD loop.

---

## Transferable patterns

### Pattern 1: The four API-preservation shapes (use the smallest one that fits)

| Cluster shape | Use this | Cost |
|---|---|---|
| Stateless or read-mostly access to host | Free function + thin delegator | 1 line of delegator per method |
| Dense internal coupling, shared computed state | Helper class with back-reference | `self.controller.<attr>` prefix everywhere |
| Public URLs / external importers must keep working | Module-level re-export | A `from .new_module import (...)` block, ~10 lines |
| Tightly bound to base class (lifecycle hooks, inherited machinery) | Leave on the original class | None — just don't move it |

Most god classes contain all four shapes simultaneously. Apply each one to the cluster that fits it. Don't try to use the helper-class pattern uniformly — small validation-style clusters become heavier than they need to be.

### Pattern 2: Migration ordering — riskiest last for rollback isolation

The four clusters were extracted in this order: validation → data_assembly → supplied_items → api. Rationale:

1. **Smallest/simplest first (validation)** to de-risk the test loop and prove the free-function pattern at lowest cost.
2. **Scale up (data_assembly)** with the same pattern, larger surface, plus the first name-mangling rewrites.
3. **Pattern shift (supplied_items)** to the helper-class pattern, on the largest cluster, with the test loop already de-risked.
4. **Highest blast radius (api) last** — the only cluster with external JS callers. Its rollback is independent of T1-T3.

The principle is general: **in a multi-step structural refactor, order extracts so that a hypothetical rollback at any point preserves the maximum amount of structural progress.** If T4 breaks an external API caller not caught by the test suite, `git revert T4` leaves the 1199-LOC reduction from T1-T3 intact. If T3 had been last and broke an external caller, you'd lose all the structural progress on rollback.

### Pattern 3: Per-commit behavior-gate enforcement

At every intermediate commit (not just the final one), run the full behavior gate (in this case 77/79 with the specific 2-failure set). Treat any deviation as a stop condition. This catches the name-mangling-style issues at the boundary where they're introduced rather than after the full extraction is layered on top.

### Pattern 4: Sample-count escalation for marginal perf cells

When a single perf-parity cell barely exceeds tolerance, **don't reach for a code explanation first.** Re-measure with 4× the samples at both ends. If the drift converges to zero, the original was a sampling artifact (see Takeaway 5). If it remains, then dig into the code.

---

## What this lesson is (and isn't)

**This lesson is:**
- A worked example of splitting a 1585-line god class using four distinct API-preservation patterns (free function, helper class with back-reference, module-level re-export, leave-on-class).
- A demonstration that the verification pipeline (spec → CDR → plan → CIR → subagent execution + measurement) scales from a method-level refactor (Lesson 02) to a class-level one without modification.
- A reference for the Python-name-mangling hazard during cross-class refactors.
- A demonstration of sample-count escalation for marginal perf-parity cells.
- The third of three lessons (warm-up → intermediate → complex).

**This lesson is not:**
- A claim that the refactor improved human comprehension (no human-comprehension study; metrics can't verify subjective claims).
- A claim that Radon MI improvements correlate with bug-rate reduction (they don't, conclusively).
- A claim that perf got *better* (it's the same — parity gate, not improvement gate).
- A claim that the 28 methods in `supplied_items.py` are now well-factored internally (they were moved verbatim; the 3 highest-CC hotspots remain at C-19/C-19/C-14 inside the helper class).
- An exhaustive cleanup of `subcontracting_controller.py` — the 2 known N+1 sites at `:145` and `:1167` were deliberately left alone (different technique, separate refactor).
- A general recommendation for the helper-class pattern over free functions — use the smallest shape that fits the cluster.

---

## Cross-references

- **Architecture review:** `../reviews/2026-05-27-erpnext-architecture-review-1.md` (Finding 1.10: god-class candidates)
- **Design spec:** `../specs/2026-05-28-subcontracting-controller-god-class-split-design.md` (4-cluster design, 22 verified assumptions, perf-parity gate definition)
- **Implementation plan:** `../plans/2026-05-28-subcontracting-controller-god-class-split-implementation-plan.md` (7 tasks, per-commit scope, behavior-gate enforcement)
- **Baseline stub:** `../measurements/03-subcontracting-controller-god-class-split-baseline.md` (T0 capture)
- **Measurement report:** `../measurements/03-subcontracting-controller-god-class-split.md` (structural metrics + cold/warm perf parity + 20-run re-measurement diagnostic)
- **Refactor commits:** `8431114719` (T0) + `88a55033` (T1) + `2b85c304a4` (T2) + `928747922a` (T2 cleanup) + `910231b8b8` (T3) + `25efc1ead4` (T4) + `583e2a7560` (T5) + `663fcd1009` (T5 re-measure)
- **Warm-up lesson:** `01-warm-up-update-payment-schedule.md`
- **Intermediate lesson:** `02-intermediate-sales-invoice-n1s.md`

---

## Next steps

Out-of-scope items from spec §6, framed as future work:

- **Batch the 2 known N+1 sites** at `subcontracting_controller.py:145` and `:1167` — different technique (collect-IDs-then-batch-query, per Lesson 02), separate refactor.
- **Refactor the 3 high-CC hotspots inside `SuppliedItemsHelper`** (`set_batch_for_supplied_items` C-19, `_set_supplied_or_received_items` C-19, `_set_batch_nos` C-14). Now isolated to a 609-line helper file — a future "Part 4" could decompose them without touching the controller.
- **Extract the stock-ledger cluster** (`update_ordered_and_reserved_qty`, `update_stock_ledger`, etc.) — currently stays on the controller because it's tightly bound to `StockController` base class machinery. A split would also touch `stock_controller.py`.
- **Apply the same patterns to `BuyingController`** (1297 L) — the next-largest controller in the directory.
- **Fix the 2 baseline test failures** (`test_secondary_items_delivery`, `test_work_order_creation_qty`) — pre-existing fixture-data issue, untouched by this refactor.
- **Add radon-based CC/MI thresholds to CI** — reporting metrics is in scope for this exercise; enforcing thresholds is a separate initiative.

The three-part series is complete. The verification pipeline carried from a single-method extraction (Lesson 01) through a multi-site batching pass (Lesson 02) to a class-level split (Lesson 03) without modification. The new ingredient at each step (measurement in Lesson 02; API-preservation patterns + name-mangling discipline + per-commit gating in Lesson 03) layered on top of the existing discipline rather than replacing it.
