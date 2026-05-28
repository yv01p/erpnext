# Lesson 2: SalesInvoice N+1 Batching (Intermediate)

This is the second of a three-part refactoring series on the ERPNext codebase. The warm-up (Lesson 1) extracted a long braided method into single-concern helpers and batched two N+1s — without measurement, the theoretical bound was stated and the lesson ended. This intermediate lesson scales the N+1 surface from 2 to 5 (across 3 methods), drops method extraction entirely (the loop bodies are small enough that 2-pass inline is the right shape), and **adds empirical before/after measurement** to replace the warm-up's "theoretical, not measured" caveat.

A note on the pivot: Lesson 1's "Next steps" anticipated a single complex method with hooks and background jobs as the next exercise. That target is deferred to Lesson 3. Instead, this lesson widens the surface — multiple N+1 sites across multiple small methods — and adds empirical measurement. The measurement discipline, in particular, turned out to be the more valuable next step after the warm-up's theoretical-only bound.

**Commits:**
- Site 1 refactor: `6c6a13ba5b` — batch `Asset.status` lookup in `validate_fixed_asset`
- Site 1 test hardening: `3144b8153d` — multi-row coverage for `validate_fixed_asset`
- Site 2 refactor: `6f103dadb7` — batch SO+DN `docstatus` lookups in `check_prev_docstatus`
- Site 3 refactor: `f4525190d3` — batch Timesheet lookups in `validate_time_sheets_are_submitted`
- Measurement script + report: `b48081cd7b` — re-runnable script and report
- Lesson: this commit
- Source spec: `docs/specs/2026-05-27-sales-invoice-n1-batching-design.md`
- Implementation plan: `docs/plans/2026-05-27-sales-invoice-n1-batching-implementation-plan.md`
- Measurement report: `docs/measurements/02-sales-invoice-n1-batching.md`
- Architecture review: `docs/reviews/2026-05-27-erpnext-architecture-review-1.md` (commit `d25d0e8d05`), Finding 3.2

---

## What we found

Five read-N+1 patterns spread across three methods on the `SalesInvoice` DocType class in `erpnext/accounts/doctype/sales_invoice/sales_invoice.py`:

| Site | Method | Line (pre-refactor) | DB calls per iteration |
|---|---|---|---|
| 1 | `validate_fixed_asset` | L412 | 1 (`Asset.status`) |
| 2 | `check_prev_docstatus` | L1394 + L1399 | 2 (Sales Order + Delivery Note `docstatus`) |
| 3 | `validate_time_sheets_are_submitted` | L879 + L887 | 2 (Timesheet Detail `sales_invoice` + Timesheet `status`) |

All three methods iterate a child-table collection (`self.get("items")` or `self.timesheets`) and call `frappe.db.get_value(...)` once per iteration to pull a single field off a linked document. None of the sites mutate state inside the loop — they are pure validation reads. None of them lock rows. None of them use the row for anything beyond comparing the fetched field against a constant and conditionally raising `frappe.throw`.

This is the textbook collect-IDs-then-batch-query shape, multiplied by five.

---

## How we identified it

The candidates came from the same architecture review that fed the warm-up, this time from **Finding 3.2** (database calls inside loops, aggregated per file). `sales_invoice.py` was flagged with 19 distinct DB-in-loop candidates — the second-highest count in the codebase after `stock_entry.py` (21). The review's representative sample included three sites:

- L412 — `validate_fixed_asset`'s per-row `Asset` fetch
- L729-730 — Timesheet unlink inside `unlink_sales_invoice_from_timesheets`
- L740-741 — POS Invoice cancel inside `cancel_pos_invoice_credit_note_generated_during_sales_invoice_mode`

Only L412 survived verification. **The other two were load-modify-save patterns, not read-N+1s.** L729 loads a full Timesheet so it can call a mutating unlink method on the instance; L740 loads a POS Invoice so it can call `.cancel()`. The architecture review's mechanical "for-loop with a Frappe DB call inside" detector found both — it can't distinguish a read-for-comparison (batchable) from a read-to-modify-then-write (not batchable in the standard way). Replacing those with bulk SQL UPDATEs is possible, but it bypasses hooks and has different correctness implications. Out of scope for this exercise.

So we re-scoped. Site 1 (`validate_fixed_asset`) stayed. Two fresh genuine read-N+1 sites — `check_prev_docstatus` and `validate_time_sheets_are_submitted` — were chosen by re-reading the file for loops whose bodies only read-and-compare. The forced re-scope is recorded in the spec's Verified Assumptions table (A4–A9).

**Pedagogical takeaway from the re-scope:** an arch review's static-pattern detector is a starting point, not a work order. Every site needs a manual eyeball before it lands in a spec.

---

## What we did

Three commits, one per site. Each commit applies the same shape:

1. **Pre-pass:** build a set of identifiers from the child collection.
2. **One batched `frappe.db.get_all` per concern**, gated on the set being non-empty.
3. **Existing loop body unchanged**, except per-row `get_value` calls become dict lookups.

No helper methods were extracted. No new imports. The methods grew modestly (Site 2's `check_prev_docstatus` went from ~13 lines to ~33; Site 3's `validate_time_sheets_are_submitted` from ~21 to ~44), but the loop body itself stayed the same length and the same shape — the growth is pre-loop scaffolding, not loop-body complexity.

### Site 1 — `validate_fixed_asset`

Before (per-row Asset fetch inside the if-chain):

```python
for d in self.get("items"):
    if d.is_fixed_asset:
        if d.asset:
            if not self.is_return:
                # asset_status is only read in the elif branches below — never on the update_stock branch
                asset_status = frappe.db.get_value("Asset", d.asset, "status")
                if self.update_stock:
                    frappe.throw(...)
                elif asset_status in ("Scrapped", "Cancelled", "Capitalized"):
                    frappe.throw(...)
                ...
```

After (single batched fetch before the loop, gated on `not self.is_return`):

```python
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
    ...
    asset_status = asset_status_map.get(d.asset)
    ...
```

Skipping the batched fetch entirely when `self.is_return` is True preserves the original's behavior of never reading `asset_status` on return invoices.

### Site 2 — `check_prev_docstatus`

Two N+1s, one method. Two separate batched fetches (one per linked DocType). The collected sets are disjoint by construction — a row's `sales_order` is unrelated to its `delivery_note` — so one batched query each, not a join.

Before:

```python
for d in self.get("items"):
    if d.sales_order and frappe.db.get_value("Sales Order", d.sales_order, "docstatus", cache=True) != 1:
        frappe.throw(_("Sales Order {0} is not submitted").format(d.sales_order))
    if d.delivery_note and frappe.db.get_value("Delivery Note", d.delivery_note, "docstatus", cache=True) != 1:
        throw(_("Delivery Note {0} is not submitted").format(d.delivery_note))
```

After:

```python
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
        frappe.throw(...)
    if d.delivery_note and dn_docstatus_map.get(d.delivery_note) != 1:
        throw(...)
```

The original used `cache=True` on the per-row `get_value`. The batched version queries fresh once and dereferences from a dict. Within a single request, the observed values are identical: the cache hit returns whatever the first miss wrote, and the batched fetch reads the same row. **The semantic difference is invisible at the call site — but the cache discipline matters during measurement.** See "What we learned" below.

### Site 3 — `validate_time_sheets_are_submitted`

Same shape as Site 2 (two N+1s, two batched fetches), with the added wrinkle of the walrus operator on the first throw branch — `if sales_invoice := frappe.db.get_value("Timesheet Detail", ..., "sales_invoice"):`. The dict-lookup version preserves the walrus exactly: `if sales_invoice := detail_invoice_map.get(data.timesheet_detail):` short-circuits on `None` or falsy `sales_invoice` field, same as the original.

Before:

```python
for data in self.timesheets:
    if data.time_sheet and data.timesheet_detail:
        if sales_invoice := frappe.db.get_value("Timesheet Detail", data.timesheet_detail, "sales_invoice"):
            frappe.throw(...)
    if data.time_sheet:
        status = frappe.db.get_value("Timesheet", data.time_sheet, "status")
        if status not in ["Submitted", "Payslip", "Partially Billed"]:
            frappe.throw(...)
```

After:

```python
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
            frappe.throw(...)
    if data.time_sheet:
        status = sheet_status_map.get(data.time_sheet)
        if status not in ["Submitted", "Payslip", "Partially Billed"]:
            frappe.throw(...)
```

### Test coverage decisions

Site 1 had no existing multi-row coverage that asserted the post-loop state; we added one targeted test (commit `3144b8153d`) that builds an SI with multiple fixed-asset items and exercises the loop. Sites 2 and 3 already had submit-path coverage that transitively exercised the loops with multiple rows in the existing fixtures; no new tests needed. The per-site decision was made at implementation time by grepping `test_sales_invoice.py`, not pre-committed in the spec.

---

## What we measured

Unlike the warm-up, this lesson is backed by empirical numbers. The measurement script (`docs/measurements/scripts/measure_n1_refactor.py`) was run twice — once at the pre-refactor commit (`db7f4a669f`, parent of the first refactor) and once at HEAD after all three refactors landed (`f4525190d3`). Per-site results at N ∈ {1, 5, 20}, median of 5 wall-clock runs after 1 warm-up iteration, local-MariaDB dev bench:

| site | method | N | queries Δ | latency Δ (ms) |
|---:|---|---:|---:|---:|
| 1 | `validate_fixed_asset`              |  1 |   0 |  +0.051 |
| 1 | `validate_fixed_asset`              |  5 |  −4 |  −1.193 |
| 1 | `validate_fixed_asset`              | 20 | −19 |  −7.127 |
| 2 | `check_prev_docstatus`              |  1 |   0 |  +0.042 |
| 2 | `check_prev_docstatus`              |  5 |  −8 |  −2.326 |
| 2 | `check_prev_docstatus`              | 20 | −38 | −12.137 |
| 3 | `validate_time_sheets_are_submitted` |  1 |   0 |  +0.058 |
| 3 | `validate_time_sheets_are_submitted` |  5 |  −8 |  −2.482 |
| 3 | `validate_time_sheets_are_submitted` | 20 | −38 | −14.357 |

**Headline numbers at N=20:**
- Site 1: **20 queries → 1**, latency Δ **−7.13ms**.
- Site 2: **40 queries → 2**, latency Δ **−12.14ms**.
- Site 3: **40 queries → 2**, latency Δ **−14.36ms**.

The query-count math is exact: per-row 1 call collapses to 1 batched call (Site 1); per-row 2 calls (one per linked DocType) collapses to 2 batched calls (Sites 2 and 3). The full per-site table, methodology, fixtures, and noise/variance analysis are in `docs/measurements/02-sales-invoice-n1-batching.md`.

### Did the measurement match the swag?

The spec recorded a pre-measurement performance swag for falsification:

| Site | Swag (queries before, per row) | Measured (queries before, per row) | Notes |
|---|---|---|---|
| 1 | 1 per fixed-asset item | 1 per item | Exact match. |
| 2 | "up to 2 per item; cache softens repeated SOs/DNs" | Exactly 2 per item | Over-performed swag. Measurement uses distinct SOs/DNs per row, so `cache=True` softening doesn't apply. |
| 3 | up to 2 per timesheet entry | Exactly 2 per entry | Exact match. |

Site 2's measured saving was larger than swag predicted because the swag assumed real-world workloads might re-use the same SO across multiple SI rows (allowing the cache to collapse repeated lookups within a request), while the measurement uses distinct SOs per row by design — adversarial input shape that defeats `cache=True`. Whether production hits the swag's expected cache softening or the measurement's worst case depends on workload patterns we can't predict here. The headline number (40 → 2 at N=20) is the worst-case bound.

### N=1 — the small penalty

At N=1 the measured latency is **higher** after the refactor than before by 40-60 µs across all three sites. The set-comprehension, `list()` cast, dict comprehension, and `frappe.db.get_all` call all cost a few µs each, even when fetching just one row. The crossover is between N=1 and N=5: by N=5 the wins are −1.2 to −2.5 ms per call.

This is consistent with the spec's "Measurement at small N may be dominated by framework overhead" caveat. The lesson here: batching is not free — there is a constant cost to the pre-pass and the batched query. It pays off the moment the per-row N×latency exceeds that constant. For these three sites and the measured ~250-400 µs per local-MariaDB query, the break-even is at N=2.

---

## What we learned

Four pedagogical takeaways. None of them are about Frappe specifically; all of them transfer to any framework with a per-call DB API and a static-analysis-driven refactoring practice.

### (a) Arch-review's "DB-in-loop" detector flags BOTH read-N+1s AND load-modify-save patterns

Mechanical pattern-matching ("for-loop with a Frappe DB call inside the body") catches the right shape but not the right semantics. Of the three sites the architecture review originally surfaced for this exercise, two were load-modify-save loops where the standard collect-IDs-then-batch-query pattern does not apply: the inner `frappe.get_doc(...)` loads a full document specifically because a mutating method gets called on the instance, then the result gets persisted per-row. You can't batch those without bypassing hooks, and bypassing hooks has different correctness implications.

**The lesson:** the detector tells you where to look, not what to do. Manual verification of loop body shape — is this a read-for-comparison or a load-to-mutate? — is mandatory before the site lands in a spec. The spec's "Verified Assumptions" table (A4-A9) records the re-scope; the brainstorming session that surfaced the issue is part of the audit trail.

### (b) `cache=True` interacts non-obviously with warm-up iterations in measurement protocols

This is the most transferable insight from the exercise, and it bit while writing the measurement script (the fourth of the five-task implementation plan) — not during the refactor itself.

The original plan was to clear `frappe.local.cache` between every measured iteration (warm-up included). This works for Sites 1 and 3, which use plain `frappe.db.get_value(doctype, name, field)` — no cache. Site 2's pre-refactor code uses `frappe.db.get_value(doctype, name, field, cache=True)`, which populates **`frappe.db.value_cache[doctype][name][field]`** — a separate cache on the DB instance, **not** `frappe.local.cache`.

The first measurement run on Site 2 showed **0 queries** for every measured iteration of the pre-refactor code, when the script expected 2N. Without clearing `frappe.db.value_cache`:
1. The warm-up iteration populated the cache for all N rows.
2. Every subsequent measured iteration hit the cache → 0 queries observed.
3. The BEFORE/AFTER comparison inverted (BEFORE looked faster than AFTER).

The fix was a two-line change to the script's `clear_cache()`: clear both `frappe.local.cache` and `frappe.db.value_cache`. This makes the BEFORE measurement reflect a cold request — the production-relevant case for the first `validate()` of a freshly-loaded Sales Invoice. The cache benefit on repeated validations within a single request is real but not measured here (acknowledged in the report's scope notes).

**Transferable lesson:** when measuring framework code that has multiple cache layers, identify every layer the measured code can touch and clear them all between iterations. A single cache type-check ("`get_value(..., cache=True)` populates the DB cache, not the local cache") is enough to derail a measurement. The bug is silent — the script ran cleanly and produced plausible-looking numbers — and only surfaces if you sanity-check the query counts against the loop count.

### (c) Inline 2-pass scales to 5 N+1s across 3 methods without helper extraction

The warm-up extracted three helpers to deal with one 114-line method braiding four concerns. This exercise did the opposite: no extraction, just pre-pass scaffolding inserted at the top of each method.

Why the difference? The warm-up method braided aggregation, validation, math, and persistence — four concerns intertwined in one body. Extraction gave each concern a single-concern home, and the orchestrator became a table-of-contents. Here, each loop body has **one** concern (validate). The N+1 isn't a sign of intertwined concerns; it's a sign of per-row data fetching that could be pre-fetched. The minimal change is to pre-fetch, not to extract.

Method-size delta:
- Site 1: 25 lines → 38 lines (+13). One pre-pass.
- Site 2: 13 lines → 33 lines (+20). Two pre-passes, two doctypes.
- Site 3: 21 lines → 44 lines (+23). Two pre-passes, two doctypes, walrus preserved.

The growth is real but uniform — the pre-pass scaffolding is identical-shape boilerplate (set-build → conditional `get_all` → dict-comprehension), repeated 1-2 times per method. No helper was extracted because the duplication is intrinsic: different doctypes, different fields, different filters. A helper would either parameterize over all four (becoming a generic `_batch_field_by_name` that exists nowhere else in the codebase and obscures the intent) or wrap each call site in a single-use helper that adds a function-call indirection for no readability benefit. Neither passes the YAGNI bar.

Sketch of the helper we considered and rejected:

```python
def _batch_field_by_name(doctype: str, names: set[str], field: str) -> dict:
    if not names:
        return {}
    return {
        r["name"]: r[field]
        for r in frappe.db.get_all(doctype, filters={"name": ("in", list(names))}, fields=["name", field])
    }
```

Every call site would then read `_batch_field_by_name("Sales Order", so_names, "docstatus")` — tighter mechanically, but each site loses its self-documenting "I'm fetching Sales Order docstatuses by name into a map" reading, and the reader has to jump to the helper to confirm semantics. With only 4-5 call sites across the file and 2-3 line bodies each, the cost of the indirection exceeds the cost of the duplication.

**Transferable lesson:** when the loop body is single-concern and small, inline 2-pass is the minimal refactor. Helper extraction is for braided methods, not for repeated patterns. The fact that the same shape repeats 5 times across 3 methods is not duplication-to-eliminate; it's a recognizable idiom that experienced readers can pattern-match.

### (d) Measured numbers may diverge from swag — and the divergence teaches

Three places the measured numbers diverged from the spec's swag:

1. **Site 2 over-performed.** Swag predicted "up to 2 per item, cache softens"; measured exactly 2 per item. The measurement's fixture construction (distinct SOs/DNs per row) defeats `cache=True`. The headline saving is real but workload-dependent in production.
2. **N=1 went slightly negative.** Swag did not predict this; measurement showed a 40-60 µs regression at N=1 due to the constant-cost pre-pass. The break-even is N=2 on this bench; it would be lower on a slower DB (WAN-attached MariaDB) and higher on a faster one.
3. **N=20 latency saving was smaller than swag predicted.** Spec swagged "20-200ms at N=20"; measurement showed 7-14ms. The swag assumed 1-5ms per query; the measured local-MariaDB per-query latency is 250-400µs. Production deployments with WAN-attached DBs will see savings closer to the spec's swag.

**Transferable lesson:** the swag is a pre-measurement hypothesis to be falsified, not a target to confirm. Each divergence teaches: workload assumptions (Site 2), constant-cost overhead (N=1), per-query latency profile of the test environment (N=20 magnitude). The discipline is recording the swag *before* measurement so the divergence is visible afterward. Recording it after is post-hoc rationalization.

---

## Verification stack used

Same pipeline as the warm-up, with one addition (measurement):

1. **Spec** (`thorough-brainstorming`) — empirically verified design. 24 verified assumptions + 5 measurement-script assumptions, all recorded in `docs/specs/2026-05-27-sales-invoice-n1-batching-design.md`.
2. **Critical Design Review (CDR)** — adversarial review of the spec. Gitignored.
3. **Plan** (`thorough-writing-plans`) — task-level implementation plan with every file path, function signature, fixture builder, and test command verified against the real codebase. Output: `docs/plans/2026-05-27-sales-invoice-n1-batching-implementation-plan.md`.
4. **Critical Implementation Review (CIR)** — adversarial review of the plan, two rounds. Gitignored.
5. **Subagent-driven execution** — five tasks (3 refactors + measurement + lesson), each in its own subagent with explicit scope and self-review. Bench tests ran green after each refactor commit.
6. **Empirical measurement** — re-runnable script, before/after capture, JSON output, written report. Replaces the warm-up's "theoretical bound from code inspection."

The lesson is the same as the warm-up's: **verify assumptions before you refactor, not after.** The new ingredient — measurement — adds **verify performance claims with numbers, not swag.**

---

## Transferable patterns

### Pattern 1: Collect-IDs-then-batch-query (with cache-discipline corollary)

The warm-up established this pattern; this lesson scales it to two-doctype loops and adds the measurement discipline.

**When:** A loop with a database call inside that queries by a single ID or name, AND the loop body only reads the fetched value (does not mutate it).

**How:**
1. Pre-pass: iterate the source collection, collect IDs into a set (use a set comprehension with the same predicate as the loop's `if` chain).
2. Gate the batched query on the set being non-empty (avoid an empty `IN ()` clause).
3. Issue `frappe.db.get_all(doctype, filters={"name": ("in", list(names))}, fields=[...])`.
4. Build `{name: row}` dict (or `{name: field}` if you only need one field).
5. Loop body unchanged, except `frappe.db.get_value(...)` → `dict_map.get(name)`.

**Cache-discipline corollary** (from "What we learned (b)"): if the original code uses `cache=True`, the per-row cache benefit on **repeated** validations within a single request is lost. In practice this is rarely observable (the second validation hits the cache once, not N times — the batched fetch is still one query). When measuring, clear both `frappe.local.cache` AND `frappe.db.value_cache` between iterations.

**Where else this applies in ERPNext** (from Finding 3.2's remaining 16 candidates in `sales_invoice.py`, plus 19 in `stock_entry.py`, 16 in `accounts_controller.py`, etc.): wherever the loop body is a pure read-and-compare. Filter out load-modify-save patterns first.

### Pattern 2: Empirical performance work needs cache hygiene

If your measurement script reports zero queries for a code path you know fires queries, look for caching before assuming a bug in the script. Frappe has at least three cache layers any given DB call might hit:

- `frappe.local.cache` — request-scoped, populated by `frappe.cache().get_value(...)` and several other paths.
- `frappe.db.value_cache` — DB-instance scoped, populated specifically by `frappe.db.get_value(..., cache=True)`.
- `frappe.db.auto_commit_on_many_writes` and other DB-instance state that affects query batching.

The measurement script clears the first two. The third doesn't apply here (validation reads don't write). Other frameworks have their own zoos — Django's `QuerySet.cache`, SQLAlchemy's `Session.identity_map`, etc. Each one is a potential ambush.

---

## What this lesson is (and isn't)

**This lesson is:**
- A worked example of inline 2-pass batching applied to 5 N+1 sites across 3 methods.
- A demonstration that the warm-up's verification pipeline (spec → review → plan → review → implement) scales with measurement appended.
- A reference for the cache-discipline gotcha when measuring `cache=True` code.
- The second of three lessons (warm-up → intermediate → complex).

**This lesson is not:**
- A claim that these three methods were production bottlenecks (no incident triggered the refactor).
- A claim that 7-14ms per `validate()` call is universally meaningful (it depends on call frequency, fixture size, DB latency).
- An exhaustive treatment of all 19 N+1s in `sales_invoice.py` (only 3 addressed; remainder is future work).
- A general defense of inline 2-pass over helper extraction (it's the right choice **when the loop body is small and single-concern**, not in general).

---

## Cross-references

- **Architecture review:** `../reviews/2026-05-27-erpnext-architecture-review-1.md` (Finding 3.2: 19 N+1s in `sales_invoice.py`)
- **Design spec:** `../specs/2026-05-27-sales-invoice-n1-batching-design.md` (5 N+1 sites, 24 verified assumptions, measurement protocol)
- **Implementation plan:** `../plans/2026-05-27-sales-invoice-n1-batching-implementation-plan.md` (5 tasks, per-site code blocks, verification steps)
- **Measurement report:** `../measurements/02-sales-invoice-n1-batching.md` (per-site results, methodology, noise/variance analysis)
- **Refactor commits:** `6c6a13ba5b` (Site 1) + `3144b8153d` (Site 1 test) + `6f103dadb7` (Site 2) + `f4525190d3` (Site 3) + `b48081cd7b` (measurement script + report)
- **Warm-up lesson:** `01-warm-up-update-payment-schedule.md`

---

## Next steps

Lesson 3 (complex) will tackle a method or method-cluster with non-trivial dependencies — likely from Finding 2's longer-method candidates or Finding 1's god-class targets. The techniques from this lesson (collect-IDs-then-batch-query, cache discipline during measurement) transfer; the new ingredient will be managing cross-method or cross-class dependencies during refactor.
