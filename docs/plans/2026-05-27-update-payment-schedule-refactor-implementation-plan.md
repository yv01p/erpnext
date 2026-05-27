# PaymentEntry.update_payment_schedule Refactor Implementation Plan

> **For agentic workers:** REQUIRED: Use `superpowers:subagent-driven-development` to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Source spec:** `docs/specs/2026-05-27-update-payment-schedule-refactor-design.md` (commit SHA: `4cc42030c9ca5ff2577c6305ef4480dbd2957944`)

**Goal:** Replace the 114-line braided `PaymentEntry.update_payment_schedule` method with a thin orchestrator + three private helpers, batching the two per-iteration DB queries (Payment Schedule fetch, conversion_rate fetch) into one-per-batch each — without altering observable behavior.

**Architecture:** One Python file modified in place. The orchestrator delegates to three new private methods on the same class: `_build_payment_term_maps` (loads + aggregates, with batched queries), `_compute_base_amounts` (pure currency math), `_apply_payment_schedule_update` (the cancel/apply SQL writes). One test method added to cover a previously unexercised throw branch.

**Tech stack:** Python 3.10+ (project targets 3.14, ruff target py310); Frappe v16 framework; `frappe.qb` query builder; `frappe.db.get_all`; MariaDB via Frappe Bench.

---

## File Structure

- **Modify:** `erpnext/accounts/doctype/payment_entry/payment_entry.py:782-894` — replace method body with orchestrator + 3 helpers.
- **Modify:** `erpnext/accounts/doctype/payment_entry/test_payment_entry.py` — add 1 test method to `TestPaymentEntry` class (closes the A6 gap from the spec).
- **Create:** `docs/lessons/01-warm-up-update-payment-schedule.md` — pedagogical write-up after the refactor lands.

## Prerequisite (outside this plan)

Frappe Bench installed and operational:
- `~/frappe-bench/` initialized; this repo symlinked as `~/frappe-bench/apps/erpnext`.
- `test_site` created with ERPNext installed.
- `bench --site test_site run-tests --module erpnext.accounts.doctype.payment_entry.test_payment_entry --lightmode` exits 0 against the unmodified codebase.

This is environment setup, not a code-change task. Tracked separately by the user.

## Inherited from spec

These are ground truth from `thorough-brainstorming`'s verification at spec-write time; not re-checked here:

- Payment Schedule doctype has columns `paid_amount, base_paid_amount, discounted_amount, outstanding, base_outstanding, payment_term, discount, discount_type`; child-table implicit `parent`/`parenttype` (spec A1).
- Payment Entry Reference doctype has `reference_doctype, reference_name, payment_term, allocated_amount, total_amount` (spec A2).
- Invoice-class reference doctypes (Sales/Purchase Invoice, Sales/Purchase Order, Dunning, Payment Entry) all have `conversion_rate`; Journal Entry doesn't — but `payment_term` filters refs to invoice-class in practice (spec A3).
- `frappe.qb.from_(...).select(...).where(...).run(as_dict=True)` works (spec A4; same-file precedent at `payment_entry.py:2181-2192`).
- `frappe.db.get_all(doctype, filters={"name": ("in", list)}, fields=[...])` returns dot-accessible rows (spec A5).
- No hooks intercept `update_payment_schedule`; only 2 call sites (`on_submit` L207, `on_cancel` L312); no test mocks the internals (spec A7-A9).
- `Tuple`, `qb`, `flt`, `get_field_precision`, `fmt_money`, `_`, `frappe` are already imported at the top of `payment_entry.py` (per spec A4 and existing method usage).

## Verified plan-level assumptions

| # | Category | Assumption | Evidence |
|---|---|---|---|
| P1 | File path | `erpnext/accounts/doctype/payment_entry/test_payment_entry.py` exists | `ls`: 69933 bytes |
| P2 | File path | `docs/reviews/2026-05-27-erpnext-architecture-review-1.md` exists (Task 3 cross-links it) | `ls`: 11798 bytes |
| P3 | Signature | `create_customer`, `create_payment_terms_template` defined in `test_payment_entry.py` (L2260, L2193); `create_sales_invoice` imported L21; `get_payment_entry` imported L11 — all callable in the new test without additional imports | Direct read of file head + L2193/L2260 |
| P4 | Signature | `pe.update_payment_schedule(cancel=0)` is callable directly (public method at L782) | `payment_entry.py:782` |
| P5 | Signature | `pe.references[0].allocated_amount = N` is a valid mutation | Existing test pattern at `test_payment_entry.py:1213` |
| P6 | Signature | `frappe.ValidationError` is what `frappe.throw` raises | Existing usage at `test_payment_entry.py:1215` |
| P7 | Command | `bench --site test_site run-tests --module DOTTED.PATH --lightmode` is the correct invocation for running all tests in a module | `.github/workflows/run-individual-tests.yml:142` uses this shape verbatim |
| P8 | Command | Same as P7 — plan uses only `--module` (no `--test` flag, which lacks evidence in this repo) | (Adjustment from initial draft — see "Plan adjustments" below) |
| P9 | Command | `python3 -m py_compile FILE` exits 0 on syntactically valid Python | Stdlib (Python 3.12 installed locally) |
| P10 | Ordering | Task 1 (test commit) precedes Task 2 (refactor) — Task 2 Step 4 runs the module and expects the new test to be present | Plan structure |
| P11 | Ordering | Task 2 (refactor) precedes Task 3 (lesson) — lesson narrates a green test run | Plan structure |
| P12 | Code validity | `ERPNextTestSuite` inherits from `unittest.TestCase`; `assertRaises(...) as ctx`, `str(ctx.exception)`, `assertIn` all available | `erpnext/tests/utils.py:4` (`import unittest`), `:2991` (`class ERPNextTestSuite(unittest.TestCase)`) |
| P13 | Code validity | `get_payment_entry` already imported at file top — local import unnecessary | `test_payment_entry.py:11` (Adjustment — see below) |
| P14 | Consumer impact | Calling `pe.update_payment_schedule(cancel=0)` with over-allocation throws BEFORE any SQL UPDATE — no persistence side effects | `payment_entry.py:867-872` (throw) precedes L875-894 (SQL UPDATE in apply branch) |
| P15 | Consumer impact | New test method as sibling of existing `TestPaymentEntry` methods triggers no discovery concerns | Existing pattern: all tests are methods of `TestPaymentEntry` (`test_payment_entry.py:29`) |
| P16 | Code validity | Add to class `TestPaymentEntry(ERPNextTestSuite)` at `test_payment_entry.py:29` | Direct read |

### Plan adjustments folded in during verification

- **P13**: Dropped the redundant `from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry` line that the initial draft included inside the test method. The file already imports it at L11.
- **P47/P8**: Initial draft used `--test test_persist_time_overallocation_throw` for single-test invocation in Task 1 Step 2. The `--test` flag lacks evidence in this repo's workflows. Switched to running the full module in both Task 1 Step 2 and Task 2 Step 4 — fast under `--lightmode`, identical pedagogical signal ("all tests pass").

---

## Tasks

### Task 1: Add test for the persist-time over-allocation throw

**Files:**
- Modify: `erpnext/accounts/doctype/payment_entry/test_payment_entry.py` — add 1 method to `TestPaymentEntry` (class declared at L29; append the new method near the end of the class).

The persist-time throw at `payment_entry.py:867-872` fires when `allocated_amount > outstanding` inside `update_payment_schedule`. Under normal `pe.submit()` flow, `validate_allocated_amount_with_latest_data` (L417) catches over-allocation first, so the persist-time check is a defensive safety net only. To test it deterministically, invoke `update_payment_schedule` directly with an engineered over-allocation state.

- [ ] **Step 1: Add the new test method.** Append to the `TestPaymentEntry` class:

  ```python
  def test_persist_time_overallocation_throw(self):
      """L867-872 is a defensive safety net for state changes between validate and persist.
      Invoke update_payment_schedule directly with an over-allocated state to exercise it."""
      create_customer()
      create_payment_terms_template()

      si = create_sales_invoice(do_not_save=1, qty=1, rate=200)
      si.payment_terms_template = "Test Receivable Template"
      si.save().submit()

      pe = get_payment_entry(si.doctype, si.name).save()
      pe.references[0].allocated_amount = pe.references[0].allocated_amount + 1000

      with self.assertRaises(frappe.ValidationError) as ctx:
          pe.update_payment_schedule(cancel=0)
      self.assertIn("Cannot allocate more than", str(ctx.exception))
  ```

- [ ] **Step 2: Run module tests locally against the unmodified codebase.**

  ```bash
  bench --site test_site run-tests --module erpnext.accounts.doctype.payment_entry.test_payment_entry --lightmode
  ```

  All tests must pass, including the new `test_persist_time_overallocation_throw`. A green run here proves the test correctly asserts existing behavior (the persist-time throw fires on engineered over-allocation against the original code).

- [ ] **Step 3: Commit the test.**

  ```bash
  git add erpnext/accounts/doctype/payment_entry/test_payment_entry.py
  git commit -m "test(payment_entry): cover persist-time over-allocation throw in update_payment_schedule"
  ```

### Task 2: Refactor `update_payment_schedule`

**Files:**
- Modify: `erpnext/accounts/doctype/payment_entry/payment_entry.py:782-894` — replace the body of `update_payment_schedule` and append three new private methods to the `PaymentEntry` class.

- [ ] **Step 1: Replace lines 782-894 of `payment_entry.py`** with the orchestrator + three helpers below. The new content is ~115 lines, replacing ~113.

  ```python
  def update_payment_schedule(self, cancel=0):
      payment_amount_map, schedule_detail_map, conversion_rate_map = (
          self._build_payment_term_maps()
      )

      for idx, (key, allocated_amount) in enumerate(payment_amount_map.items(), 1):
          if not schedule_detail_map.get(key):
              frappe.throw(_("Payment term {0} not used in {1}").format(key[0], key[1]))

          allocated_amount = self.get_allocated_amount_in_transaction_currency(
              allocated_amount, key[2], key[1]
          )
          base_paid_amount, base_outstanding, discounted_amt, outstanding = (
              self._compute_base_amounts(
                  allocated_amount,
                  schedule_detail_map[key],
                  conversion_rate_map[(key[2], key[1])],
              )
          )
          self._apply_payment_schedule_update(
              idx, key, allocated_amount, discounted_amt,
              base_paid_amount, base_outstanding, outstanding, cancel,
          )

  def _build_payment_term_maps(self):
      payment_amount_map = {}
      ref_total_amount = {}
      parent_set = set()
      doctype_to_names = {}

      for ref in self.get("references"):
          if not ref.payment_term or not ref.reference_name:
              continue
          key = (ref.payment_term, ref.reference_name, ref.reference_doctype)
          payment_amount_map.setdefault(key, 0.0)
          payment_amount_map[key] += ref.allocated_amount
          ref_total_amount.setdefault((ref.reference_name, ref.reference_doctype), ref.total_amount)
          parent_set.add(ref.reference_name)
          doctype_to_names.setdefault(ref.reference_doctype, set()).add(ref.reference_name)

      if not parent_set:
          return {}, {}, {}

      PS = frappe.qb.DocType("Payment Schedule")
      ps_rows = (
          frappe.qb.from_(PS)
          .select(PS.parent, PS.payment_term, PS.outstanding, PS.discount, PS.discount_type)
          .where(PS.parent.isin(list(parent_set)))
      ).run(as_dict=True)

      conversion_rate_map = {}
      for ref_doctype, names in doctype_to_names.items():
          rows = frappe.db.get_all(
              ref_doctype,
              filters={"name": ("in", list(names))},
              fields=["name", "conversion_rate"],
          )
          for r in rows:
              conversion_rate_map[(ref_doctype, r.name)] = r.conversion_rate

      parent_to_doctypes = {}
      for (ref_name, ref_doctype) in ref_total_amount:
          parent_to_doctypes.setdefault(ref_name, []).append(ref_doctype)

      schedule_detail_map = {}
      for row in ps_rows:
          for ref_doctype in parent_to_doctypes.get(row.parent, []):
              entry_key = (row.payment_term, row.parent, ref_doctype)
              entry = schedule_detail_map.setdefault(entry_key, {})
              entry["outstanding"] = row.outstanding
              if row.discount_type and row.discount:
                  total_amount = ref_total_amount[(row.parent, ref_doctype)]
                  if row.discount_type == "Percentage":
                      entry["discounted_amt"] = total_amount * (row.discount / 100)
                  else:
                      entry["discounted_amt"] = row.discount

      return payment_amount_map, schedule_detail_map, conversion_rate_map

  def _compute_base_amounts(self, allocated_amount, schedule_entry, conversion_rate):
      outstanding = flt(schedule_entry.get("outstanding"))
      discounted_amt = flt(schedule_entry.get("discounted_amt"))
      base_paid_amount_precision = get_field_precision(
          frappe.get_meta("Payment Schedule").get_field("base_paid_amount")
      )
      base_outstanding_precision = get_field_precision(
          frappe.get_meta("Payment Schedule").get_field("base_outstanding")
      )
      base_paid_amount = flt(
          (allocated_amount - discounted_amt) * conversion_rate, base_paid_amount_precision
      )
      base_outstanding = flt(allocated_amount * conversion_rate, base_outstanding_precision)
      return base_paid_amount, base_outstanding, discounted_amt, outstanding

  def _apply_payment_schedule_update(
      self, idx, key, allocated_amount, discounted_amt,
      base_paid_amount, base_outstanding, outstanding, cancel,
  ):
      if cancel:
          frappe.db.sql(
              """
              UPDATE `tabPayment Schedule`
              SET
                  paid_amount = `paid_amount` - %s,
                  base_paid_amount = `base_paid_amount` - %s,
                  discounted_amount = `discounted_amount` - %s,
                  outstanding = `outstanding` + %s,
                  base_outstanding = `base_outstanding` - %s
              WHERE parent = %s and payment_term = %s""",
              (
                  allocated_amount - discounted_amt,
                  base_paid_amount,
                  discounted_amt,
                  allocated_amount,
                  base_outstanding,
                  key[1],
                  key[0],
              ),
          )
      else:
          if allocated_amount > outstanding:
              frappe.throw(
                  _("Row #{0}: Cannot allocate more than {1} against payment term {2}").format(
                      idx, fmt_money(outstanding), key[0]
                  )
              )

          if allocated_amount and outstanding:
              frappe.db.sql(
                  """
                  UPDATE `tabPayment Schedule`
                  SET
                      paid_amount = `paid_amount` + %s,
                      base_paid_amount = `base_paid_amount` + %s,
                      discounted_amount = `discounted_amount` + %s,
                      outstanding = `outstanding` - %s,
                      base_outstanding = `base_outstanding` - %s
                  WHERE parent = %s and payment_term = %s""",
                  (
                      allocated_amount - discounted_amt,
                      base_paid_amount,
                      discounted_amt,
                      allocated_amount,
                      base_outstanding,
                      key[1],
                      key[0],
                  ),
              )
  ```

- [ ] **Step 2: Syntax sanity.**

  ```bash
  python3 -m py_compile erpnext/accounts/doctype/payment_entry/payment_entry.py
  ```

  Must exit 0.

- [ ] **Step 3: SQL byte-equality check.**

  ```bash
  git diff erpnext/accounts/doctype/payment_entry/payment_entry.py
  ```

  In the diff, locate the two `UPDATE \`tabPayment Schedule\` SET …` blocks now inside `_apply_payment_schedule_update`. Confirm they show **only indentation changes** vs. the originals at L846-865 (cancel branch) and L875-894 (apply branch) — no character changes inside the SQL strings or parameter tuples. Spec invariant #1.

- [ ] **Step 4: Run module tests locally.**

  ```bash
  bench --site test_site run-tests --module erpnext.accounts.doctype.payment_entry.test_payment_entry --lightmode
  ```

  Every test must pass — notably `test_persist_time_overallocation_throw` (from Task 1, proves the throw still fires) and `test_overallocation_validation_on_payment_terms` at L1190 (proves the validate-phase check is undisturbed).

- [ ] **Step 5: Commit the refactor.**

  ```bash
  git add erpnext/accounts/doctype/payment_entry/payment_entry.py
  git commit -m "refactor(payment_entry): batch DB queries in update_payment_schedule and split into helpers"
  ```

- [ ] **Step 6: Push to trigger CI** as redundant verification across parallel test partitions:

  ```bash
  git push
  ```

### Task 3: Write the warm-up lesson

**Files:**
- Create: `docs/lessons/01-warm-up-update-payment-schedule.md` — new pedagogical write-up.

Write only after Task 2's local tests are green; the lesson's narrative depends on the refactor having landed.

- [ ] **Step 1: Create `docs/lessons/` if absent; write the lesson.** Content sections (each with a short code snippet or cross-link where appropriate):

  - **What we found:** the 114-line method's four interleaved concerns (aggregate → validate → currency math → persist), and the two N+1 patterns (L795 per-parent Payment Schedule fetch, L832 per-reference `conversion_rate` fetch). Include the original method excerpt (or its outline).
  - **How we identified it:** three metrics from the arch review — length (Finding 2), DB-in-loop count (Finding 3). Cross-link `docs/reviews/2026-05-27-erpnext-architecture-review-1.md`.
  - **What we did:** extracted into a thin orchestrator + 3 private helpers; replaced per-iteration DB calls with batched queries using existing codebase patterns (`frappe.qb` + `Tuple/.isin`; `frappe.db.get_all(filters={"name": ("in", ...)})`).
  - **Why this approach (and not others):** YAGNI — declined Approach A (unify cancel/apply branches via sign-flip) because the duplication wasn't in the original findings; declined Approach C (single helper) because the orchestrator would have stayed too long. Cross-link the spec.
  - **Query-count delta (theoretical, not measured):** before = `1 + N_unique_parents + N_aggregated_keys` queries; after = `1 + 1 + (1..3)` queries. Pattern, not metric.
  - **The A6 caveat:** persist-time throw at L867-872 wasn't test-covered before this refactor; Task 1 added that coverage. Why it mattered.
  - **Verification stack used:** spec (`thorough-brainstorming`) → CDR → plan (`thorough-writing-plans`) → TDD-ish loop (test-first for the gap, refactor, test again). Brief mention, not a tutorial.
  - **Transferable patterns:** "thin orchestrator + named helpers" for long braided methods; "collect identifiers in pass 1, batch query, dict lookup in the existing loop" for N+1. Both recur across the codebase.

- [ ] **Step 2: Commit.**

  ```bash
  git add docs/lessons/01-warm-up-update-payment-schedule.md
  git commit -m "docs: add warm-up lesson — refactoring update_payment_schedule"
  ```

---

## Tasks NOT in this plan

(Inherited from the source spec's "Out of scope" section, preserving its bullet form.)

- Unifying the cancel/apply SQL UPDATE branches via a sign parameter (considered as Approach A, dropped per YAGNI — not in the named findings).
- Hoisting the `frappe.get_meta("Payment Schedule").get_field(...)` precision lookups out of the loop (metadata lookup, Frappe-cached, not a DB call).
- Any change to `validate_allocated_amount_with_latest_data` at L417 (the validate-phase over-allocation check that mirrors the persist-time check at L867-872).

A new spec → new plan cycle is required to add any of these.

## Known issues inherited from spec

(Inherited verbatim from the source spec's "Known issues, accepted as out of scope" section.)

- The persist-time over-allocation throw at L867-872 (preserved verbatim in `_apply_payment_schedule_update`) is not directly exercised by the broader test suite (it's now exercised by the new test added in Task 1). The validate-phase check at L417 catches the same condition under normal flow; the persist-time throw fires only on state-change-between-validate-and-submit. The byte-for-byte copy makes regression unlikely.
