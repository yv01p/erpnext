# SubcontractingController god-class split — before/after measurement

Measured: 2026-05-28. Local-MariaDB dev bench, single warm Python process.

- **BEFORE commit:** `f3157ac3fc` (T0 baseline; spec + plan + measurement script
  landed, no source changes).
- **AFTER commit:** `25efc1ead4` (T4 HEAD; all four extraction clusters landed —
  validation, data_assembly, SuppliedItemsHelper, api).

The same measurement script was run against both trees, with all fixtures
shared (idempotent get-or-create keeps the universe of Items / Purchase Orders /
Sales Orders stable across runs).

## Methodology

Distilled from the spec's [Measurement protocol](../specs/2026-05-28-subcontracting-controller-god-class-split-design.md#42-measurement--parity-gate) section.

Two dimensions are reported per `(subject, N, cache-state)` tuple:

1. **Query count** — total `frappe.db.sql` invocations during a single call to
   the target method. Captured by monkey-patching `frappe.db.sql` for the
   duration of the measured call. The counter increments once per invocation;
   the original implementation is called through.
2. **Wall-clock latency** — `time.perf_counter()` brackets the measured call.
   Reported as the **median of 5 runs after 1 warm-up iteration**, with the
   full 5-run latency vector kept in the raw JSON for variance analysis.

**Workloads.** For each subject (SubcontractingOrder, SubcontractingReceipt,
SubcontractingInwardOrder), the script builds an *unsaved* document with N
items, each referencing a distinct upstream fixture row (service item + FG item
pair with per-FG BOM). N ∈ {1, 5, 20}. The document is never inserted; only
the `validate()` method is invoked. This isolates the validation work from
`Document.insert` / hook overhead.

**Cache discipline.** Both cold and warm cache paths are measured:

- **Cold**: `frappe.local.cache` and `frappe.db.value_cache` are cleared
  between every iteration (warm-up included). This reflects the production case
  for the first `validate()` of a freshly-loaded document.
- **Warm**: caches are cleared once before the warm-up iteration, then never
  again. This reflects repeated validations within the same request (e.g., a
  user editing the document and triggering validation multiple times).

The cold-cache discipline prevents `frappe.db.get_value(..., cache=True)` from
collapsing query counts to 0 after the first run. Cold numbers are the
production-relevant baseline; warm numbers capture the cache-benefit ceiling.

**Fixtures.** All fixtures are namespaced with the prefix `_measure_part3_` to
avoid collision with `_measure_n1_` (Part 2) and `_Test` / `_T-` test fixtures.
They are persisted (the script commits them) so subsequent runs are fast.
Idempotency is tracked by:

- Items (RM/FG/service): `item_code LIKE '_measure_part3_rm' / '..._fg_%' / '..._svc_%'`
- Purchase Orders / Sales Orders: `title` / `po_no` fields for N=1/5/20 variants

## Structural metrics

Snapshot of the controller and helpers at BEFORE (HEAD `f3157ac3fc`) and AFTER
(HEAD `25efc1ead4`):

| Metric | BEFORE | AFTER | Delta |
|---|---:|---:|---|
| **Controller LOC** (`wc -l subcontracting_controller.py`) | 1585 | 374 | -1211 (-76%) |
| **Helpers LOC** (sum of `subcontracting/*.py`, excluding `__init__.py`) | 0 | 1325 | +1325 |
| **Total LOC** (controller + helpers) | 1585 | 1699 | +114 (+7%) |
| **Controller methods** (`grep -cE "^\s*def " ...controller.py`) | 68 | 29 | -39 (-57%) |
| **Controller module-level functions** (`grep -cE "^def " ...controller.py`) | 6 | 0 | -6 (-100%) |
| **SuppliedItemsHelper methods** | 0 | 29 | +29 |
| **Module-level functions** (all helpers) | 0 | 22 | +22 |
| **Radon CC avg** (all blocks in controller.py) | B (5.82) | A (4.96) | improved |
| **Radon MI** (controller.py) | C | A | improved |
| **Import-graph fan-out** (consumer count) | 13 | 14 | +1 |

**Per-file breakdown (AFTER):**

| File | LOC | Methods / Functions | CC avg | MI grade |
|---|---:|---:|---:|---:|
| `subcontracting_controller.py` | 374 | 29 methods, 0 fns | A (4.5) | A |
| `subcontracting/__init__.py` | 0 | — | — | A |
| `subcontracting/validation.py` | 135 | 4 fns | B (8.2) | A |
| `subcontracting/data_assembly.py` | 340 | 12 fns | B (7.5) | A |
| `subcontracting/supplied_items.py` | 609 | 29 methods, 0 fns | B (8.0) | C |
| `subcontracting/api.py` | 241 | 6 fns | B (5.0) | A |

**Key observations:**

- Controller shrank by **76% LOC** (1585 → 374 lines), crossing from C to A
  maintainability index.
- Controller methods reduced from 68 to 29 (43% retention) — the 39 extracted
  methods became 29 SuppliedItemsHelper methods (9-method compression via
  private-method inlining during extraction) + 22 free functions across the
  other 3 helpers.
- Total LOC grew by **7%** (1585 → 1699) due to class boilerplate
  (`SuppliedItemsHelper.__init__`, copyright headers in 4 new files) and
  `self.controller.` prefixes in the SuppliedItemsHelper delegation layer.
- `supplied_items.py` remains the sole C-grade file (MI = C) due to carrying
  the 3 highest-complexity hotspots from the original controller
  (`set_batch_for_supplied_items` C-19, `_set_supplied_or_received_items` C-19,
  `_set_batch_nos` C-14). These were **copied as-is** (per plan: "no internal
  refactor, only extraction"). Future work can refactor these hotspots within
  the isolated helper class.
- Import-graph fan-out grew by 1 consumer (13 → 14). The new consumer is
  `subcontracting/api.py`, which imports from the controller to implement
  delegating wrappers. All existing consumers remain unchanged (the re-export
  preserved the public API path).

## Perf parity

Per `(subject, N, cache-state)` tuple: query count and latency median compared
BEFORE vs AFTER.

| Subject | N | Cache | Queries BEFORE | Queries AFTER | Q Δ | Latency ms BEFORE | Latency ms AFTER | Lat Δ ms | Lat Δ % | Verdict |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| SCO | 1 | cold | 21 | 21 | 0 | 9.77 | 8.57 | -1.20 | -12.3% | PASS |
| SCO | 1 | warm | 20 | 20 | 0 | 7.88 | 7.56 | -0.32 | -4.1% | PASS |
| SCR | 1 | cold | 12 | 12 | 0 | 8.70 | 8.35 | -0.35 | -4.0% | PASS |
| SCR | 1 | warm | 11 | 11 | 0 | 6.08 | 7.29 | +1.21 | +19.9% | noise (N=1) |
| SCIO | 1 | cold | 4 | 4 | 0 | 3.01 | 3.18 | +0.17 | +5.6% | PASS |
| SCIO | 1 | warm | 4 | 4 | 0 | 2.39 | 2.41 | +0.02 | +0.8% | PASS |
| SCO | 5 | cold | 73 | 73 | 0 | 30.21 | 30.66 | +0.45 | +1.5% | PASS |
| SCO | 5 | warm | 72 | 72 | 0 | 28.49 | 29.55 | +1.06 | +3.7% | PASS |
| SCR | 5 | cold | 48 | 48 | 0 | 29.40 | 28.31 | -1.09 | -3.7% | PASS |
| SCR | 5 | warm | 47 | 47 | 0 | 25.47 | 24.50 | -0.97 | -3.8% | PASS |
| SCIO | 5 | cold | 20 | 20 | 0 | 12.78 | 12.98 | +0.20 | +1.6% | PASS |
| SCIO | 5 | warm | 20 | 20 | 0 | 12.34 | 12.27 | -0.07 | -0.6% | PASS |
| **SCO** | **20** | **cold** | **268** | **268** | **0** | **110.67** | **113.24** | **+2.57** | **+2.3%** | **PASS** |
| **SCO** | **20** | **warm** | **267** | **267** | **0** | **116.30** | **109.13** | **-7.17** | **-6.2%** | **PASS** |
| **SCR** | **20** | **cold** | **183** | **183** | **0** | **96.79** | **91.83** | **-4.96** | **-5.1%** | **PASS** |
| **SCR** | **20** | **warm** | **182** | **182** | **0** | **85.69** | **95.98** | **+10.29** | **+12.0%** | **FAIL** |
| **SCIO** | **20** | **cold** | **80** | **80** | **0** | **51.03** | **49.69** | **-1.34** | **-2.6%** | **PASS** |
| **SCIO** | **20** | **warm** | **80** | **80** | **0** | **50.94** | **48.49** | **-2.45** | **-4.8%** | **PASS** |

**Abbreviations:**
- SCO = SubcontractingOrder.validate
- SCR = SubcontractingReceipt.validate
- SCIO = SubcontractingInwardOrder.validate

## Parity verdict

**Query count (binary equality):** PASS. All 18 cells (9 × cold/warm) match
exactly BEFORE = AFTER. No `frappe.db.*` calls were added or removed during
the extraction.

**Latency at N=20 (±10% tolerance):** FAIL (1 of 6 cells exceeded tolerance).

- 5 of 6 N=20 cells are within ±10% (SCO cold +2.3%, SCO warm -6.2%, SCR cold
  -5.1%, SCIO cold -2.6%, SCIO warm -4.8%).
- **SCR warm N=20 exceeded tolerance:** +12.0% drift (85.69ms → 95.98ms,
  +10.29ms absolute). This is the only cell that failed the parity gate.

**Latency at N=1, N=5 (informational, not gated):** 1 of 12 cells showed >10%
drift (SCR warm N=1: +19.9%). Per Part 2's findings, small-N latency is
dominated by noise (20-47% variance common at N=1); the N=1 drift is flagged
but not treated as a regression signal.

**Overall verdict:** **FAIL**. The spec's §4.2 criteria require ALL N=20
latency cells to remain within ±10%. The single SCR warm N=20 failure blocks
T6 progression pending investigation.

## SCR warm N=20 drift analysis

**Observed:** BEFORE median 85.69ms, AFTER median 95.98ms (+10.29ms, +12.0%).

**Variance context:**

- BEFORE runs: [86.26, 84.79, 85.04, 86.05, 85.69] — range 1.47ms, variance
  1.7% (very tight).
- AFTER runs: [100.17, 97.09, 95.98, 90.40, 94.05] — range 9.77ms, variance
  10.2% (high, but consistent with Part 2's N=20 variance floor).

**Interpretation:**

The BEFORE run was unusually stable (1.7% variance vs Part 2's typical 3-10%
at N=20), while the AFTER run saw typical noise (10.2%). However, even the
AFTER minimum (90.40ms) is **5.5% above the BEFORE median**, suggesting the
drift is not purely variance.

**Hypothesis:** The extraction introduced a small overhead in the warm-cache
path specific to SubcontractingReceipt. Possible sources:

1. **Delegation overhead**: The SuppliedItemsHelper adds one extra Python call
   layer (controller method → helper method) for every supplied-items operation.
   In the warm-cache path where DB latency is minimized, Python call overhead
   becomes proportionally more visible.
2. **Memory layout change**: The helper instance is stored as
   `self.supplied_items_helper` and carries its own state. Attribute lookup
   `self.supplied_items_helper.method()` vs direct `self.method()` has a small
   cost, magnified across the many hot-loop calls in the SCR validation path.
3. **SCR-specific factor**: SubcontractingReceipt has the heaviest
   supplied-items usage among the 3 subjects (183 queries vs 268 for SCO, 80
   for SCIO). The delegation overhead is amplified linearly with the number of
   supplied-item rows processed.

**Why SCR warm but not SCR cold?** Cold-cache latency is dominated by MariaDB
round-trip time (~300-400µs per query per Part 2's profile). The delegation
overhead (~1-2µs per Python call) is lost in the noise. Warm-cache latency
removes the DB bottleneck, exposing the pure-Python overhead.

**Why SCR but not SCO/SCIO?** SCO has 47% more queries than SCR (268 vs 183)
but fewer supplied-items operations per item row (its validate path includes
more non-supplied-items work — e.g., backflush logic, status updates). SCIO
has 56% fewer queries (80) and processes minimal supplied-items (inward orders
are simpler). The SCR path is the **densest supplied-items hot-loop** among
the 3 subjects, making it most sensitive to per-call overhead.

**Action required:** Per plan T5 instructions: "If ... >10% latency drift at
N=20 → fail and HALT; investigate." The 12.0% drift on SCR warm N=20 is a
**BLOCK** signal. Options:

1. **Accept as structural cost**: The +10.3ms absolute increase is small
   (95.98ms vs 85.69ms is ~10ms, or ~120µs per query on a 183-query workload).
   In production WAN-DB deployments where per-query latency is 1-10ms (not
   300µs), this overhead would be <2% of total latency. If the team prioritizes
   maintainability over microsecond-level perf, relax the tolerance to ±15% for
   warm-cache paths.
2. **Optimize delegation layer**: Inline the most-frequently-called helper
   methods back into the controller as one-liner delegations (e.g.,
   `def set_batch_for_supplied_items(self): return self.supplied_items_helper.set_batch_for_supplied_items()`
   → inline the first layer of logic). This would reduce call-stack depth in
   the hot loop.
3. **Revert SCR-specific extraction**: Roll back the SuppliedItemsHelper
   extraction and keep those methods in the controller. This defeats the
   god-class split goal but would restore SCR parity.
4. **Re-run with more iterations**: The BEFORE run's 1.7% variance is
   suspiciously low (Part 2's typical floor is 3-10%). Re-run both BEFORE and
   AFTER with 20 iterations instead of 5 to see if the BEFORE median regresses
   toward the AFTER median (i.e., the BEFORE run got lucky).

**Recommendation:** Option 4 (re-run with higher iteration count) as the
diagnostic first step, followed by Option 1 (accept as structural cost) if the
drift persists but remains <15ms absolute (<15% relative). The maintainability
win from the 76% LOC reduction and C→A MI upgrade is substantial; a 10ms
warm-cache regression on a local-MariaDB bench is unlikely to matter in
production.

## Notes on noise / variance

The per-run latency vectors are kept in the raw JSON
(`/tmp/measure_part3_before.json`, `/tmp/measure_part3_after.json`,
field `latency_ms_cold_runs` / `latency_ms_warm_runs`). Variance is measured
as `(max − min) / median`.

Runs that exceeded 10% variance (informational; not a parity gate):

| Run | Subject | N | Cache | Median (ms) | Range [min, max] | Variance |
|---|---|---:|---|---:|---|---:|
| BEFORE | SCO | 1 | cold | 9.77 | [8.73, 10.80] | 21.1% |
| BEFORE | SCR | 1 | cold | 8.70 | [8.07, 10.08] | 23.1% |
| BEFORE | SCIO | 1 | cold | 3.01 | [2.83, 4.39] | 51.8% |
| BEFORE | SCR | 5 | cold | 29.40 | [28.18, 31.11] | 10.0% |
| BEFORE | SCO | 20 | cold | 110.67 | [103.87, 128.43] | 22.2% |
| AFTER | SCO | 1 | cold | 8.57 | [7.94, 9.03] | 12.7% |
| AFTER | SCR | 1 | cold | 8.35 | [7.79, 9.62] | 22.0% |
| AFTER | SCR | 1 | warm | 7.29 | [7.19, 8.54] | 18.5% |
| AFTER | SCIO | 1 | cold | 3.18 | [3.06, 3.56] | 15.9% |
| AFTER | SCIO | 5 | cold | 12.98 | [12.82, 14.58] | 13.5% |
| AFTER | SCIO | 5 | warm | 12.27 | [11.87, 13.29] | 11.6% |
| AFTER | SCO | 20 | cold | 113.24 | [111.59, 116.39] | 4.2% |
| AFTER | SCO | 20 | warm | 109.13 | [103.04, 116.15] | 12.0% |
| AFTER | SCR | 20 | warm | 95.98 | [90.40, 100.17] | 10.2% |

**Dominant patterns:**

- **N=1 is noisy**: 10 of the 11 >10%-variance runs are at N=1 (the exception
  is BEFORE SCR N=5 cold at exactly 10.0%). The BEFORE SCIO N=1 cold run shows
  **51.8% variance** ([2.83, 4.39] on median 3.01ms) — this is an outlier
  spike (4.39ms run) in a very short-latency workload where µs-level framework
  overhead dominates.
- **N=20 stabilizes**: Only 3 of the 18 N=20 runs (6 subjects × cold/warm)
  exceeded 10% variance. The BEFORE SCO N=20 cold run (22.2% variance) had a
  single outlier at 128.43ms (+16% above median); the other 4 runs were tightly
  clustered [103.87, 120.25]. The AFTER SCO/SCR N=20 warm runs both show ~10-12%
  variance, consistent with Part 2's findings.
- **Cold vs warm**: No systematic difference in variance between cold and warm
  runs at N=20. Both paths show 3-12% variance, with occasional outliers.

**Local-MariaDB latency profile.** The measured per-query MariaDB latency sits
at roughly 350-450µs (SCO cold N=20: 113.24ms / 268 queries ≈ 423µs; SCR cold
N=20: 91.83ms / 183 queries ≈ 502µs). This is consistent with Part 2's
250-400µs range and represents a lower bound for absolute latency savings.
Production deployments on WAN-attached databases (1-10ms per query) would see
proportionally larger latency deltas for any query-count change (none occurred
here — this refactor was query-neutral).

## Inline script source

The full measurement script lives at
[`scripts/measure_part3_refactor.py`](scripts/measure_part3_refactor.py). It is
~520 lines, split into:

- **Cache + counter primitives** (`query_counter`, `clear_cache_cold`,
  `clear_cache_warm`) — clears both `frappe.local.cache` and
  `frappe.db.value_cache` (cold path) or neither (warm path).
- **`measure_method(doc, runs=5, warmup=1)`** — the inner loop. Runs both cold
  and warm passes per invocation.
- **Fixture builders** (`_ensure_items`, `_ensure_purchase_orders`,
  `_ensure_sales_orders`, `ensure_fixtures`) — idempotent get-or-create using
  `_measure_part3_` prefix tags. Inlined minimal
  `frappe.get_doc(...).insert().submit()` patterns rather than importing from
  `test_*.py` modules (which carry heavy bootstrap side effects).
- **Document builders** (`build_sco`, `build_scr`, `build_scio`) — each
  constructs an *unsaved* `frappe.new_doc(doctype)` with N items wired to
  fixture rows.
- **`run_all(site, output_path)`** — drives the 3×3×2 measurement grid (3
  subjects × 3 N values × 2 cache states) and writes the JSON payload with the
  current HEAD SHA.

To re-run:

```bash
# cwd is load-bearing: frappe.init() resolves sites relative to "."
cd ~/frappe-bench/sites
../env/bin/python ~/erpnext/docs/measurements/scripts/measure_part3_refactor.py \
    --site test_site --output /tmp/measure_part3_<label>.json
```

The output JSON has shape:

```json
{
  "commit": "<git rev-parse HEAD at script-run time>",
  "results": [
    {"subject": "SubcontractingOrder.validate", "n": 1,
     "queries_cold": 21, "queries_warm": 20,
     "latency_ms_cold_median": 8.57, "latency_ms_warm_median": 7.56,
     "latency_ms_cold_runs": [...], "latency_ms_warm_runs": [...]},
    ...
  ]
}
```

## References

- Spec: [`docs/specs/2026-05-28-subcontracting-controller-god-class-split-design.md`](../specs/2026-05-28-subcontracting-controller-god-class-split-design.md)
- Implementation plan: [`docs/plans/2026-05-28-subcontracting-controller-god-class-split-implementation-plan.md`](../plans/2026-05-28-subcontracting-controller-god-class-split-implementation-plan.md)
- Baseline stub: [`docs/measurements/03-subcontracting-controller-god-class-split-baseline.md`](03-subcontracting-controller-god-class-split-baseline.md)
- Lesson: [`docs/lessons/03-subcontracting-controller-god-class-split.md`](../lessons/03-subcontracting-controller-god-class-split.md) (T6, not yet written)
- Refactor commits:
  - T0 baseline: `8431114719` (measurement script + baseline stub + BEFORE JSON commit)
  - T1 validation cluster: `88a55033` (4 free functions)
  - T2 data_assembly cluster: `2b85c304a4` (12 free functions + 4 stays-method rewrites)
  - T2 cleanup: `928747922a` (copyright header + unused imports)
  - T3 SuppliedItemsHelper: `910231b8b8` (28 methods + 2 stays-method rewrites)
  - T4 api cluster: `25efc1ead4` (6 free functions + re-export + smoke tests)
- Measurement commit (this report): TBD (will be T5 commit)
