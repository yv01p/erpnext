# SubcontractingController god-class split — BASELINE (pre-extraction)

Captured: 2026-05-28. Local-MariaDB dev bench, single warm Python process.
Branch: `refactoring`. HEAD SHA: `f3157ac3fc8613c5ba1d1ea30ac22fa72d841ad2`.

## Purpose

T0 of the Part 3 refactor sequence locks in three reference vectors **before any
controller code moves**, so that T5's AFTER pass can compare apples-to-apples:

1. **Behavior gate** — the 4-suite subcontracting test pass counts.
2. **Structural gate** — wc/grep/radon snapshot of
   `erpnext/controllers/subcontracting_controller.py` so a later "this file
   shrank from X to Y" claim is verifiable.
3. **Perf gate** — query count + wall-clock for the three subclasses' `validate()`
   methods at N ∈ {1, 5, 20}, both cold and warm cache paths. This lets T5
   detect any regression from the extraction.

Nothing in this commit touches the controller source. The script
`scripts/measure_part3_refactor.py` and this stub are the only deliverables.

## Test baseline

All 4 suites at HEAD `f3157ac3fc` against `test_site`, run via
`bench --site test_site run-tests --module <M> --lightmode`:

| Suite | Pass / Total |
|---|---|
| `erpnext.controllers.tests.test_subcontracting_controller` | 19 / 19 |
| `erpnext.subcontracting.doctype.subcontracting_order.test_subcontracting_order` | 16 / 16 |
| `erpnext.subcontracting.doctype.subcontracting_receipt.test_subcontracting_receipt` | 32 / 32 |
| `erpnext.subcontracting.doctype.subcontracting_inward_order.test_subcontracting_inward_order` | 10 / 12 |
| **Total** | **77 / 79** |

Two known failures in `test_subcontracting_inward_order` (not regressions —
they fail on the baseline tree, unrelated to this refactor):

- `test_secondary_items_delivery`
- `test_work_order_creation_qty`

Both fail with `DoesNotExistError('Subcontracting BOM SB-0001 not found')`, an
environment-data prerequisite issue in the test_site bootstrap. T5 must
reproduce **exactly the same 77 pass / 2 named failures** to claim
behavior-preservation.

## Structural baseline

Snapshot of `erpnext/controllers/subcontracting_controller.py` at HEAD
`f3157ac3fc`:

| Metric | Value |
|---|---|
| Line count (`wc -l`) | 1585 |
| Indented `def ` count (methods) | 68 |
| Top-level `def ` count (module-level functions) | 6 |
| Radon Maintainability Index (`radon mi`) | **C** |
| Radon avg cyclomatic complexity (`radon cc -a`) | **B (5.82)** over 67 blocks |
| Consumers (`grep` for `erpnext.controllers.subcontracting_controller`) | 13 files |

**Hotspots (C-grade methods, complexity 19):**

- `SubcontractingController.__update_consumed_materials` (line 407, C=19)
- `SubcontractingController.set_batch_for_supplied_items` (line 749, C=19)
- `SubcontractingController.__set_supplied_or_received_items` (line 946, C=19)
- `SubcontractingController.calculate_additional_costs` (line 1282, C=19)
- `SubcontractingController.get_available_materials` (line 472, C=18)
- `SubcontractingController.validate_items` (line 144, C=17)

The 4 C-19 methods are explicit refactor targets across T1-T3 (see Part 3
design doc §3.6).

## Perf baseline

Numbers captured by
`docs/measurements/scripts/measure_part3_refactor.py` against `test_site` at
HEAD `f3157ac3fc`. 5 measured runs + 1 warm-up per (subject, N, cache-state).

Median latency in milliseconds; query count is the deterministic post-warmup
total (we monkey-patch `frappe.db.sql`). Cache discipline matches Part 2: cold
clears `frappe.local.cache` and `frappe.db.value_cache` between every
iteration; warm clears them once and never again.

| Subject | N | Cold queries | Cold ms (median) | Warm queries | Warm ms (median) |
|---|---|---|---|---|---|
| `SubcontractingOrder.validate` | 1 | 21 | 9.77 | 20 | 7.88 |
| `SubcontractingReceipt.validate` | 1 | 12 | 8.70 | 11 | 6.08 |
| `SubcontractingInwardOrder.validate` | 1 | 4 | 3.01 | 4 | 2.39 |
| `SubcontractingOrder.validate` | 5 | 73 | 30.21 | 72 | 28.49 |
| `SubcontractingReceipt.validate` | 5 | 48 | 29.40 | 47 | 25.47 |
| `SubcontractingInwardOrder.validate` | 5 | 20 | 12.78 | 20 | 12.34 |
| `SubcontractingOrder.validate` | 20 | 268 | 110.67 | 267 | 116.30 |
| `SubcontractingReceipt.validate` | 20 | 183 | 96.79 | 182 | 85.69 |
| `SubcontractingInwardOrder.validate` | 20 | 80 | 51.03 | 80 | 50.94 |

Cold-vs-warm delta is small (1 query, ~10-30% latency) — the validate paths
are dominated by per-row DB work that no cache mediates. The N=1→20 query
scaling is roughly linear-in-N (e.g. SCO goes 21 → 73 → 268; growth factor
~12.7× over 20× workload, consistent with per-row N+1 patterns), confirming
the per-row hot-loop hypothesis the refactor design rests on. T5 should show
the same linearity (the refactor is structural; it does not change query
shape) and within ±10% of these median latencies; any large delta is a
regression to investigate.

Raw JSON (full 5-run latency vectors per row) is regenerable via the
[script](scripts/measure_part3_refactor.py); not committed to the repo.

## Methodology pointer

Measurement script: [`scripts/measure_part3_refactor.py`](scripts/measure_part3_refactor.py).

It shares shape with Part 2's
[`measure_n1_refactor.py`](scripts/measure_n1_refactor.py); deltas:

1. Reports BOTH cold and warm cache paths per `(subject, N)` (Part 2 only
   reported cold).
2. Subjects are the 3 Subcontracting subclasses' `validate()` methods, not
   SalesInvoice methods.
3. Fixture-name prefix is `_measure_part3_` (Part 2 uses `_measure_n1_`) so
   the two scripts' fixtures can coexist in `test_site` without collision.

Fixture strategy: 20 distinct `_measure_part3_svc_NN` / `_measure_part3_fg_NN`
Item pairs with per-FG BOMs (each FG has a single customer-provided RM
`_measure_part3_rm`) so that POs of N=1, 5, 20 all have N **distinct** service
items (PO validator rejects duplicate item rows). Sales Orders for the SCIO
path use the same Item pairs. Per-N parent PO/SO docs are tagged via
`title` / `po_no` for idempotent reuse across runs.

## Baseline commit SHA

```
f3157ac3fc8613c5ba1d1ea30ac22fa72d841ad2
```

T5 must compare AFTER measurements against this SHA's outputs.
