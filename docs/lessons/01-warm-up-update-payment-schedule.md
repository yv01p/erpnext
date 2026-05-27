# Lesson 1: Refactoring `PaymentEntry.update_payment_schedule`

This is the first of a three-part refactoring series on the ERPNext codebase. Each lesson demonstrates a different technique applied to a real production method flagged during an architecture review. This warm-up lesson covers extracting a long braided method into single-concern helpers and batching two N+1 database query patterns.

**Commits:**
- Test: `cb98cb2efc` — added coverage for persist-time over-allocation throw
- Refactor: `601f3f1a1b` — split into orchestrator + helpers and batched queries
- Source spec: `docs/specs/2026-05-27-update-payment-schedule-refactor-design.md`
- Implementation plan: `docs/plans/2026-05-27-update-payment-schedule-refactor-implementation-plan.md`

---

## What we found

The original `PaymentEntry.update_payment_schedule` method was 114 lines (L782-894 of `payment_entry.py` before the refactor). It handled four interleaved concerns in a single method body:

1. **Aggregation** — loop over `self.references`, group by `(payment_term, reference_name, reference_doctype)` key, sum `allocated_amount` per key
2. **Validation** — check that each aggregated key exists in the target invoice's Payment Schedule table; throw if not
3. **Currency math** — apply discounts, convert amounts using the reference document's `conversion_rate`, compute base amounts with field-specific precision
4. **Persistence** — write two different SQL UPDATE statements (cancel path vs. apply path) to the `tabPayment Schedule` table

The method also contained two N+1 database query patterns:

**N+1 Pattern 1 (Line 795):** Inside the first loop, for each unique `reference_name` encountered, fetch all Payment Schedule rows for that parent:

```python
payment_schedule = frappe.get_all(
    "Payment Schedule",
    filters={"parent": ref.reference_name},
    fields=[
        "paid_amount",
        "payment_amount",
        "payment_term",
        "discount",
        "outstanding",
        "discount_type",
    ],
)
```

This query fired once per unique parent document, not once per reference row — but still linear in the number of distinct parent documents being paid against.

**N+1 Pattern 2 (Line 832):** In the second loop, for each aggregated key, fetch the `conversion_rate` from the reference document:

```python
conversion_rate = frappe.db.get_value(key[2], {"name": key[1]}, "conversion_rate")
```

This query fired once per aggregated key. In typical usage (one payment against multiple invoices), that meant one query per invoice.

Both patterns were discovered during the architecture review:
- Method length flagged as **Finding 2** (methods >100 lines)
- First N+1 flagged as **Finding 3** (database calls inside loops)

The second N+1 was not explicitly listed in the original finding but was spotted during spec-writing and folded into the refactor as a second demonstration.

---

## How we identified it

The candidate method was surfaced by the architecture review documented in `../reviews/2026-05-27-erpnext-architecture-review-1.md`. Three metrics drove the selection:

1. **Method length** (Finding 2) — `PaymentEntry.update_payment_schedule` at 114 lines was in the top quartile of long methods in the `PaymentEntry` class
2. **Database calls in loops** (Finding 3) — the Payment Schedule fetch at L795 was explicitly called out
3. **Pedagogical fit** — as a relatively self-contained method with clear extract boundaries, it made a good warm-up example

The architecture review used static analysis (line counts, cyclomatic complexity proxies via branch counts, and manual inspection for database calls inside loop bodies). No performance profiling was involved — this was pattern-spotting, not hotspot-hunting.

---

## What we did

The refactor split the 114-line method into four pieces:

1. **Orchestrator** — `update_payment_schedule(self, cancel=0)` at ~20 lines. Calls three helpers in sequence, iterates over the aggregated data, delegates currency math and persistence.

2. **Helper 1: `_build_payment_term_maps`** — returns `(payment_amount_map, schedule_detail_map, conversion_rate_map)`. Handles all aggregation and data loading in four passes:
   - Pass 1: iterate `self.references`, aggregate `allocated_amount` by key, collect parent names and reference doctypes
   - Pass 2: one batched Payment Schedule query for all parents at once
   - Pass 3: batched `conversion_rate` queries, one per distinct reference doctype
   - Pass 4: build the schedule detail map by joining the fetched rows with the aggregated keys

3. **Helper 2: `_compute_allocation_amounts`** — pure arithmetic. Takes `allocated_amount`, `schedule_entry`, and `conversion_rate` as inputs; returns `(base_paid_amount, base_outstanding, discounted_amt, outstanding)`. No database calls. Identical logic to the original L829-843.

4. **Helper 3: `_apply_payment_schedule_update`** — houses the cancel/apply SQL UPDATE branch from the original L845-894. Includes the over-allocation throw (L867-872) and the `if allocated_amount and outstanding` guard. Not unified across the two branches.

### Batching strategy

**For Payment Schedule rows:**

Original pattern (once per parent):
```python
if not invoice_paid_amount_map.get(key):
    payment_schedule = frappe.get_all(
        "Payment Schedule",
        filters={"parent": ref.reference_name},
        fields=[...],
    )
```

Batched pattern (once total):
```python
parent_set = {ref.reference_name for ref in self.get("references") if ref.payment_term}
PS = frappe.qb.DocType("Payment Schedule")
ps_rows = (
    frappe.qb.from_(PS)
    .select(PS.parent, PS.payment_term, PS.outstanding, PS.discount, PS.discount_type)
    .where(PS.parent.isin(list(parent_set)))
).run(as_dict=True)
```

The codebase already used `frappe.qb` with `.isin()` for batch queries in multiple files (`accounts/utils.py`, `controllers/buying_controller.py`). This pattern matched the local idiom.

**For conversion_rate:**

Original pattern (once per aggregated key):
```python
conversion_rate = frappe.db.get_value(key[2], {"name": key[1]}, "conversion_rate")
```

Batched pattern (once per reference doctype):
```python
conversion_rate_map = {}
for ref_doctype, names in doctype_to_names.items():
    rows = frappe.db.get_all(
        ref_doctype,
        filters={"name": ("in", list(names))},
        fields=["name", "conversion_rate"],
    )
    for r in rows:
        conversion_rate_map[(ref_doctype, r.name)] = r.conversion_rate
```

This pattern also had precedent in the codebase (`purchase_invoice.py`, `selling_controller.py`, `buying_controller.py`).

---

## Why this approach (and not others)

During spec-writing, three approaches were considered:

**Approach A: Unify cancel/apply branches via sign-flip**

The cancel and apply paths execute nearly identical SQL UPDATEs, differing only in the sign of the operations (`+= paid_amount` vs. `-= paid_amount`, etc.). We could have unified them into a single UPDATE with a `sign = -1 if cancel else 1` parameter.

**Decision:** Rejected per YAGNI. The duplication was not called out in the architecture review findings. The two branches are readable as-is. Unifying them would add a parameter, a multiplication in the SQL string formatting, and cognitive load for future readers who need to mentally expand the sign-flip. The finding was "method too long + N+1s," not "branch duplication." Keep scope tight.

**Approach B: Three helpers (aggregation + math + persist)**

Split into `_build_payment_term_maps` (load + aggregate), `_compute_allocation_amounts` (pure math), `_apply_payment_schedule_update` (persist). The orchestrator stays under 25 lines; each helper has a single concern; the N+1s are batched in the first helper.

**Decision:** Selected. This was the approach implemented. It directly addressed the named findings (length + N+1s), stayed within the original method's scope, and introduced no new concepts beyond what the codebase already used.

**Approach C: Single helper**

Pull everything except the outer loop into one large helper. The orchestrator would be ~10 lines, but the helper would be 90+ lines — not much better than the original.

**Decision:** Rejected. Doesn't solve the length problem, only moves it.

Cross-reference: `../specs/2026-05-27-update-payment-schedule-refactor-design.md` section "Out of scope" for the full rationale.

---

## Query-count delta (theoretical)

This refactor does not include measured performance data. The query-count change is a theoretical bound based on code inspection.

**Before:**
- 1 query (implicit, start of method context)
- + N_unique_parents queries (Payment Schedule fetch, once per distinct `reference_name`)
- + N_aggregated_keys queries (`conversion_rate` fetch, once per aggregated `(payment_term, reference_name, reference_doctype)`)
- Total: `1 + N_unique_parents + N_aggregated_keys`

**After:**
- 1 query (implicit, start of method context)
- + 1 batched Payment Schedule query (all parents at once)
- + 1..3 batched `conversion_rate` queries (once per distinct reference_doctype; typically 1-2 in practice, at most 6)
- Total: `1 + 1 + (1..3)` = 3-5 queries

**In concrete terms:**
- If a payment applies to 10 invoices with 30 aggregated keys, the original fires ~41 queries; the refactor fires ~3.
- The delta grows linearly with the number of invoices and payment terms.

No timing data was collected. This is a pattern demonstration, not a performance tuning exercise. The architecture review flagged the pattern as a maintainability risk (hard to reason about, hard to change) and a potential scaling bottleneck. The refactor addresses both.

---

## The A6 caveat

The spec identified one untested behavior: the over-allocation throw at L867-872 of the original method (now preserved in `_apply_payment_schedule_update`).

**The code:**
```python
if allocated_amount > outstanding:
    frappe.throw(
        _("Row #{0}: Cannot allocate more than {1} against payment term {2}").format(
            idx, fmt_money(outstanding, currency=self.payment_currency), key[0]
        )
    )
```

**The issue:** This throw fires during persistence (after validation has passed). The validate-phase check at L417 (`validate_allocated_amount_with_latest_data`) catches over-allocation under normal flow. The persist-time throw is a defensive safety net for state-change-between-validate-and-submit scenarios (e.g., concurrent edits, direct DB updates, hooks that mutate state).

**Why it mattered:** The test suite had no direct coverage of this branch. If the refactor broke the persist-time throw, CI would pass, and the regression would surface only in production under race conditions. To prevent this, Task 1 of the implementation plan added a test (`test_persist_time_overallocation_throw` at commit `cb98cb2efc`) that bypasses validation and directly invokes `update_payment_schedule` with over-allocated amounts.

**Post-refactor status:** The throw is now in `_apply_payment_schedule_update`, copied byte-for-byte from the original. The new test exercises it. The gap is closed.

This is the "untested defensive branch" pattern: code that exists to catch edge cases outside the happy path, but has no test coverage because the happy path dominates. Refactoring forces you to decide whether to preserve it blindly or cover it first. Here, we covered it first.

---

## Verification stack used

This refactor was not ad-hoc. It followed a five-step verification pipeline:

1. **Spec** (`thorough-brainstorming`) — empirically verified design. Every assumption about the codebase (field names, query patterns, call sites, test structure) was checked before the spec was finalized. Output: `docs/specs/2026-05-27-update-payment-schedule-refactor-design.md`.

2. **Critical Design Review (CDR)** — adversarial review of the spec. Checked for logic holes, missed edge cases, unverified assumptions, and scope creep. The CDR file is gitignored (local-only artifact), but findings were addressed before moving to planning.

3. **Plan** (`thorough-writing-plans`) — task-level implementation plan with every file path, function signature, and test command verified against the real codebase. Output: `docs/plans/2026-05-27-update-payment-schedule-refactor-implementation-plan.md`.

4. **Critical Implementation Review (CIR)** — adversarial review of the plan. Checked for incorrect task ordering, missing verification steps, and plan-time bugs. Also gitignored.

5. **TDD-ish loop** — test-first for the A6 gap (commit `cb98cb2efc`), then refactor (commit `601f3f1a1b`), then re-run the full test suite. CI via GitHub Actions (`.github/workflows/server-tests-mariadb.yml`) ran the entire 2271-line `test_payment_entry.py` suite against MariaDB.

The lesson is not "use this exact pipeline every time." The lesson is "verify assumptions before you refactor, not after." The spec and plan stages forced explicit statements about what the code does today, which helpers are needed, and where the batch boundaries are. The reviews caught two issues before implementation (one incorrect filter assumption, one missing edge case). The test-first step closed the untested-branch gap.

For a 114-line method, this is over-engineering. For a 3000-line class in production with 80 methods and 433 branches, it's risk mitigation.

---

## Transferable patterns

Two patterns from this refactor recur across the ERPNext codebase and are broadly applicable:

### Pattern 1: Thin orchestrator + named helpers

**When:** You have a long method (>80 lines) that braids multiple concerns. Extract-method refactorings are obvious but you're unsure where to cut.

**How:**
1. Identify distinct concerns (aggregation, validation, math, I/O, persistence).
2. Extract each concern into a private helper with a descriptive name (`_build_maps`, `_compute_amounts`, `_apply_update`).
3. Leave the original method as a thin orchestrator that calls the helpers in order.
4. Keep the orchestrator's line count under 30. If it grows beyond that, you missed an extraction.

**Why it works:** The orchestrator becomes the table-of-contents for the method's behavior. A reader can understand the flow without reading the helpers. When debugging, you can step into the relevant helper and ignore the others. When extending, you modify one helper without touching the orchestrator or the other helpers.

**Where else this applies in ERPNext:**
- `AccountsController` methods over 100 lines (Finding 2 of the arch review lists 47 candidates)
- `StockEntry.validate` (200+ lines, mixes serial/batch validation, GL validation, and workflow checks)
- `SalesInvoice.on_submit` (150+ lines, posts to GL, updates stock, triggers notifications, advances status)

### Pattern 2: Collect-IDs-then-batch-query for N+1s

**When:** You have a loop with a database call inside that queries by a single ID or name.

**How:**
1. First pass: iterate your source data, collect all IDs/names into a set.
2. After the loop: issue one batched query with `WHERE id IN (...)` or `filters={"name": ("in", list)}`.
3. Second pass: iterate the source data again, look up the fetched rows in a dict keyed by ID.

**Code sketch:**
```python
# BEFORE (N queries)
for item in items:
    detail = frappe.db.get_value("SomeDoctype", item.ref_id, "field")
    process(item, detail)

# AFTER (1 query)
ref_ids = {item.ref_id for item in items}
details = frappe.db.get_all("SomeDoctype", filters={"name": ("in", list(ref_ids))}, fields=["name", "field"])
detail_map = {d.name: d.field for d in details}
for item in items:
    process(item, detail_map[item.ref_id])
```

**Why it works:** Databases are fast at set operations, slow at round-trips. One query returning 100 rows is orders of magnitude faster than 100 queries returning 1 row each, even if the total data transferred is identical. The pattern trades memory (storing the set and the dict) for latency.

**Where else this applies in ERPNext:**
- `get_bin` calls inside `StockEntry` item loops (Finding 3, line 1647)
- Per-item `get_incoming_rate` in `StockReconciliation` (Finding 3, line 284)
- Per-reference `get_outstanding_amount` in `PaymentReconciliation` loops

Both patterns are mechanical. Once you spot the signal (long method, query-in-loop), the refactor is straightforward. The hard part is spotting them in the first place. Architecture reviews and static analysis help.

---

## What this lesson is (and isn't)

**This lesson is:**
- A worked example of extract-method + batch-query refactoring on a real production codebase
- A demonstration of verified, disciplined refactoring workflow (spec → review → plan → review → implement → test)
- A reference for the "thin orchestrator" and "batch the N+1" patterns
- The first of three lessons in a refactoring series (warm-up → intermediate → complex)

**This lesson is not:**
- A performance tuning guide (no before/after timings, no profiling, no load testing)
- A claim that this method was a bottleneck (it wasn't flagged by any production incident or profiler)
- A recommendation to apply this level of rigor to every 100-line method (cost/benefit varies by context)
- A finished state for `PaymentEntry` (the class is still 3500 lines with 80 methods; this touched 1 of them)

The goal was to learn a repeatable process for refactoring high-risk code without breaking production. The outcome was a working refactor, test coverage for an untested branch, and two transferable patterns. The next two lessons will apply the same process to more complex methods with more intricate dependencies.

---

## Cross-references

- **Architecture review:** `../reviews/2026-05-27-erpnext-architecture-review-1.md` (Finding 2: methods >100 lines; Finding 3: N+1 at L795)
- **Design spec:** `../specs/2026-05-27-update-payment-schedule-refactor-design.md` (approach selection, verified assumptions, behavior invariants)
- **Implementation plan:** `../plans/2026-05-27-update-payment-schedule-refactor-implementation-plan.md` (task breakdown, verification steps)
- **Test commit:** `cb98cb2efc` (`test_persist_time_overallocation_throw` in `test_payment_entry.py`)
- **Refactor commit:** `601f3f1a1b` (orchestrator + 3 helpers in `payment_entry.py`)

---

## Next steps

Lesson 2 (intermediate) will tackle a method with external dependencies — one that calls out to other DocTypes, hooks, and background jobs. The challenge there is not just extraction, but isolation and dependency injection.

Lesson 3 (complex) will cover a method that spans multiple transactions and has non-obvious rollback semantics.

Both are in the architecture review's Finding 2 candidate list. The techniques from this warm-up transfer directly.
