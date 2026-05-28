# SalesInvoice N+1 batching — before/after measurement

Measured: 2026-05-28. Local-MariaDB dev bench, single warm Python process.

- **BEFORE commit:** `db7f4a669f` (parent of first refactor; spec + plan landed,
  no source changes).
- **AFTER commit:** `f4525190d3` (HEAD; all three refactors landed — sites 1, 2, 3).

The same measurement script was run against both trees, with all fixtures
shared (idempotent get-or-create keeps the universe of Assets / Sales Orders /
Delivery Notes / Timesheets stable across runs).

## Methodology

Distilled from the spec's [Measurement protocol](../specs/2026-05-27-sales-invoice-n1-batching-design.md#measurement-protocol) section.

Two dimensions are reported per `(site, N)` tuple:

1. **Query count** — total `frappe.db.sql` invocations during a single call to
   the target method. Captured by monkey-patching `frappe.db.sql` for the
   duration of the measured call. The counter increments once per invocation;
   the original implementation is called through.
2. **Wall-clock latency** — `time.perf_counter()` brackets the measured call.
   Reported as the **median of 5 runs after 1 warm-up iteration**, with the
   full 5-run latency vector kept in the raw JSON for variance analysis.

**Workloads.** For each site, the script builds an *unsaved* Sales Invoice
with N items (or N timesheet entries for site 3), each referencing a distinct
upstream fixture row. N ∈ {1, 5, 20}. The SI is never inserted; only the
target validation method is invoked. This isolates the method's own work
from `Document.insert` / hook overhead.

**Cache discipline.** Both `frappe.local.cache` *and* `frappe.db.value_cache`
are cleared between every iteration (warm-up included). The latter is
load-bearing for site 2: the pre-refactor code uses
`frappe.db.get_value(..., cache=True)`, which populates
`frappe.db.value_cache[doctype][name][field]`. Without this clear, the warm-up
iteration would populate the cache and every measured iteration would report
**0 queries**, inverting the BEFORE/AFTER comparison. This makes the BEFORE
numbers reflect the cold-cache case — the production-relevant case for the
first `validate()` of a freshly-loaded Sales Invoice. The original code's
benefit from cache hits on *repeated* validations within the same request is
real but not measured here (scope choice flagged in the spec).

**Fixtures.** All fixtures are namespaced with the prefix `_measure_n1_` to
avoid collision with `_Test ` / `_T-` test fixtures. They are persisted (the
script commits them) so subsequent runs are fast. Idempotency is tracked by:

- Assets: `asset_name LIKE '_measure_n1_asset_%'`
- Sales Orders / Delivery Notes: `po_no LIKE '_measure_n1_so_%' / '..._dn_%'`
  (the customer-PO field, overloaded as an idempotency tag)
- Timesheets: `note LIKE '_measure_n1_ts_%'`

## Per-site results

| site | method | N | queries before | queries after | queries Δ | latency_ms before | latency_ms after | latency Δ |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | `validate_fixed_asset`              |  1 |  1 | 1 |   0 |  0.368 | 0.419 |  +0.051 |
| 1 | `validate_fixed_asset`              |  5 |  5 | 1 |  −4 |  1.641 | 0.448 |  −1.193 |
| 1 | `validate_fixed_asset`              | 20 | 20 | 1 | −19 |  7.743 | 0.616 |  −7.127 |
| 2 | `check_prev_docstatus`              |  1 |  2 | 2 |   0 |  0.718 | 0.760 |  +0.042 |
| 2 | `check_prev_docstatus`              |  5 | 10 | 2 |  −8 |  3.241 | 0.915 |  −2.326 |
| 2 | `check_prev_docstatus`              | 20 | 40 | 2 | −38 | 13.376 | 1.238 | −12.137 |
| 3 | `validate_time_sheets_are_submitted` |  1 |  2 | 2 |   0 |  0.701 | 0.759 |  +0.058 |
| 3 | `validate_time_sheets_are_submitted` |  5 | 10 | 2 |  −8 |  3.374 | 0.892 |  −2.482 |
| 3 | `validate_time_sheets_are_submitted` | 20 | 40 | 2 | −38 | 15.593 | 1.236 | −14.357 |

### Reading the numbers

- **Site 1 (`validate_fixed_asset`)** — exactly N queries before (per-row
  `frappe.db.get_value("Asset", d.asset, "status")`), exactly 1 query after.
  At N=20 the saving is **19 queries / 7ms** per SI validation.
- **Site 2 (`check_prev_docstatus`)** — exactly 2N queries before (one per SO,
  one per DN, no row in the test SI shares an SO or DN), exactly 2 queries
  after (one batched fetch per side). At N=20 the saving is **38 queries /
  12ms** per SI validation. The 2N=0 (`cache=True`) collapse was prevented by
  clearing `frappe.db.value_cache` (see Methodology).
- **Site 3 (`validate_time_sheets_are_submitted`)** — exactly 2N queries before
  (one Timesheet Detail lookup + one Timesheet lookup per timesheet entry),
  exactly 2 queries after. At N=20 the saving is **38 queries / 14ms** per SI
  validation.

The query Δ matches the spec's [Performance expectation](../specs/2026-05-27-sales-invoice-n1-batching-design.md#performance-expectation-pre-measurement-swag) exactly for sites 1 and 3.
Site 2 over-performed the swag: the swag predicted "up to 2 per item" and
"cache softens repeated SOs/DNs"; the measured 2N pattern reflects (a) the
script's workload uses distinct SOs/DNs per row (cache cannot collapse them)
and (b) `cache=True` is invalidated each iteration (the cache softening only
matters within a single request that calls validation N>1 times — not the
first-validation case this measurement targets).

### N=1 is dominated by noise, not the win

At N=1 the AFTER median is *higher* than the BEFORE median by 40–60 µs across
all three sites. This is well within wall-clock noise (the N=1 BEFORE runs
have 23-47% variance — see "Notes on noise / variance" below) and is consistent
with the spec's "Measurement at small N may be dominated by ... framework
overhead" caveat: the post-refactor path adds a set-comprehension, a `list(...)`
cast, a `dict` comprehension and a `frappe.db.get_all` call, all of which cost
a few µs even when fetching just 1 row. The win materializes the moment N>1
and grows linearly.

## Notes on noise / variance

The per-run latency vectors are kept in the raw JSON
(`/tmp/measure_before.json`, `/tmp/measure_after.json`,
field `latency_ms_runs`). Variance is measured as `(max − min) / median`.

Runs that exceeded the spec's >20% variance flag:

| run | site | N | median (ms) | range (ms) | variance |
|---|---:|---:|---:|---|---:|
| BEFORE | 1 |  1 |  0.368 | [0.321, 0.493] | 46.8% |
| BEFORE | 2 |  1 |  0.718 | [0.653, 0.824] | 23.9% |
| BEFORE | 3 |  5 |  3.374 | [3.211, 3.916] | 20.9% |
| BEFORE | 1 | 20 |  7.743 | [6.392, 8.761] | 30.6% |
| AFTER  | 1 |  1 |  0.419 | [0.407, 0.556] | 35.5% |
| AFTER  | 3 | 20 |  1.236 | [1.159, 1.421] | 21.2% |

The dominant pattern is occasional outlier spikes inside the 5-run vector
(e.g., BEFORE site 1 N=1: `[0.493, 0.376, 0.368, 0.336, 0.321]` — the first
post-warm-up run is ~50% above the median; BEFORE site 1 N=20: `[6.39, 8.76,
7.69, 7.74, 7.90]` — the second run is an outlier 13% above the median).
These are noise spikes on a busy dev machine (background processes, kernel
scheduling, MariaDB page-cache state), not measurement bugs. The median
absorbs them well — even the worst outlier-corrupted run is still a fraction
of the BEFORE/AFTER delta at N=20.

**Local-MariaDB latency profile.** The measured per-query MariaDB latency
sits at roughly 250-400 µs (Site 1 BEFORE N=20: 7.7ms / 20 queries ≈ 385µs
per query; Site 2 BEFORE N=20: 13.4ms / 40 queries ≈ 335µs per query). This
is a lower bound for the savings — production deployments on WAN-attached
databases see millisecond-range per-query latency, scaling the latency
savings reported here by 3-10×.

## Inline script source

The full measurement script lives at
[`scripts/measure_n1_refactor.py`](scripts/measure_n1_refactor.py). It is
~450 lines, split into:

- **Cache + counter primitives** (`query_counter`, `clear_cache`) — clears
  both `frappe.local.cache` and `frappe.db.value_cache`.
- **`measure_method(si, method_name, runs=5, warmup=1)`** — the inner loop.
- **Fixture builders** (`_ensure_assets`, `_ensure_sales_orders`,
  `_ensure_delivery_notes`, `_ensure_timesheets`, `ensure_fixtures`) —
  idempotent get-or-create using `_measure_n1_` prefix tags. Inlined
  minimal `frappe.get_doc(...).insert().submit()` patterns rather than
  importing from `test_*.py` modules (which carry heavy
  `ERPNextTestSuite` bootstrap side effects).
- **SI builders** (`build_site1_invoice`, `build_site2_invoice`,
  `build_site3_invoice`) — each constructs an *unsaved* `frappe.new_doc("Sales
  Invoice")` with N items / timesheets wired to fixture rows.
- **`run_all_sites(site, output_path)`** — drives the 3×3 measurement grid
  and writes the JSON payload with the current HEAD SHA.

To re-run:

```bash
# cwd is load-bearing: frappe.init() resolves sites relative to "."
cd ~/frappe-bench/sites
../env/bin/python ~/erpnext/docs/measurements/scripts/measure_n1_refactor.py \
    --site test_site --output /tmp/measure_<commit>.json
```

The output JSON has shape:

```json
{
  "commit": "<git rev-parse HEAD at script-run time>",
  "results": [
    {"site": 1, "method": "validate_fixed_asset", "n": 1,
     "queries": 1, "latency_ms_median": 0.41, "latency_ms_runs": [...]},
    ...
  ]
}
```

## References

- Spec: [`docs/specs/2026-05-27-sales-invoice-n1-batching-design.md`](../specs/2026-05-27-sales-invoice-n1-batching-design.md)
- Implementation plan: [`docs/plans/2026-05-27-sales-invoice-n1-batching-implementation-plan.md`](../plans/2026-05-27-sales-invoice-n1-batching-implementation-plan.md)
- Lesson: [`docs/lessons/02-intermediate-sales-invoice-n1s.md`](../lessons/02-intermediate-sales-invoice-n1s.md) (commits `190745e017` + clarity polish `e9652c2d98`)
- Refactor commits:
  - Site 1 (`validate_fixed_asset`): `6c6a13ba5b` + hardened test `3144b8153d`
  - Site 2 (`check_prev_docstatus`): `6f103dadb7`
  - Site 3 (`validate_time_sheets_are_submitted`): `f4525190d3`
- Measurement commit (this report + script): `b48081cd7b`
