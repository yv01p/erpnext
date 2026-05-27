# Critical Design Review: 2026-05-27-update-payment-schedule-refactor-design (Round 1)

**Spec:** `/home/yv01p/erpnext/docs/specs/2026-05-27-update-payment-schedule-refactor-design.md`
**Verified Assumptions section:** present

## 1. Verified-assumptions cross-check

| # | Assumption | Status (fresh read) |
|---|---|---|
| A1 | Payment Schedule has the columns the SQL UPDATEs touch | Reconfirmed. `payment_schedule.json` shows `paid_amount`, `base_paid_amount`, `discounted_amount`, `outstanding`, `base_outstanding`, `payment_term`, `discount`, `discount_type`; `istable: 1` so `parent`/`parenttype` are implicit. |
| A2 | Payment Entry Reference has the needed fields | Reconfirmed. `payment_entry_reference.json` shows `reference_doctype`, `reference_name`, `payment_term`, `allocated_amount`, `total_amount`. |
| A3 | All invoice-class reference doctypes have `conversion_rate` | Reconfirmed. Sales Invoice, Purchase Invoice, Sales Order, Purchase Order, Dunning, Payment Entry all have it; Journal Entry does not — but `payment_term` is invoice-class in practice, matching the original's implicit constraint. |
| A4 | `frappe.qb` batched-query pattern works | Reconfirmed. Same-file precedent at `erpnext/accounts/doctype/payment_entry/payment_entry.py:2181-2192` uses `frappe.qb.from_(...).select(...).where(...).run(as_dict=True)`. Single-column `.isin([...])` precedents at `erpnext/accounts/utils.py:2009`, `:2345`. |
| A5 | `frappe.db.get_all(..., filters={"name": ("in", list)}, fields=[...])` returns dot-accessible rows | Reconfirmed. Multiple precedents (purchase_invoice.py:1924, selling_controller.py:97/265/784, buying_controller.py:149/1217). |
| A6 | Over-allocation test exercises persist-time throw | Reconfirmed: NO. The test uses `pe.save()` (validate path, hits L417), not `pe.submit()`. Persist-time throw at L867-872 is a defensive safety net; preserved byte-for-byte. |
| A7 | No hooks intercept the method | Reconfirmed: `hooks.py` has zero refs; no external callers. |
| A8 | Only two call sites, no dynamic dispatch | Reconfirmed: `payment_entry.py:207` (on_submit), `:312` (on_cancel, cancel=1); no `getattr`/string-name calls. |
| A9 | No test mocks the internals | Reconfirmed. |
| A10 | Empty-input guards are sufficient | Reconfirmed. Early return `({}, {}, {})` when `parent_set` is empty; orchestrator's `for` loop is a no-op on empty `payment_amount_map`, so neither `schedule_detail_map` nor `conversion_rate_map` is indexed. |

All ten reconfirmed.

## 2. Literal-wrongness findings

No literal-wrongness findings.

Traced the following candidates and dropped each:

- **Original fetches `paid_amount` and `payment_amount` (L799-800) — refactor drops them.** Neither field is read in the original's body (only `term.payment_term`, `term.outstanding`, `term.discount_type`, `term.discount` are accessed). Dropping unused fields is behavior-preserving. Not a finding.
- **First-wins semantics for `ref_total_amount`.** Original's `if not invoice_paid_amount_map.get(key)` skip-fetch uses the 3-tuple key; refactor's `ref_total_amount.setdefault((reference_name, reference_doctype), ...)` uses the 2-tuple. Traced cases for: same-key/different-total, same-(parent, doctype)/different-payment-term, mixed-doctype/same-parent. In every case, the surviving `total_amount` value is the same first-ref's value because the original's "skip if any 3-tuple-keyed entry exists for this parent" semantics collapse to "skip on second ref with this (parent, doctype)." Not a finding.
- **Dropped fields cause the batched query to return fewer bytes.** Pure performance side-effect, not behavior.
- **Edge case: record deleted between validate and on_submit (missing `conversion_rate`).** Original returns `None` from `frappe.db.get_value`, then errors on `None * float` (TypeError). Refactor's orchestrator indexes `conversion_rate_map[(key[2], key[1])]` and errors with `KeyError`. Different exception class, same workflow position (both abort `on_submit`). Spec's invariant #6 covers value-equality for existing records and doesn't extend to deleted-record exception types; the literal-wrongness test is not met because the asked-for behavior — `on_submit` aborts when state is impossible — holds. Not a finding.
- **Per-iteration `frappe.qb` semantics under REPEATABLE READ.** Frappe's default MariaDB isolation gives all reads in a single transaction a consistent snapshot. Original's N fetches and refactor's 1 fetch return identical data in normal operation. Not a finding.

## 3. Forced decisions

No forced decisions found.

## 4. Recommendation

✅ **Approve as-is.** §2 and §3 are both empty. Spec is ready for implementation planning.
