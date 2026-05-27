# Design: Refactor `PaymentEntry.update_payment_schedule`

**Scope:** one file (`erpnext/accounts/doctype/payment_entry/payment_entry.py`), one method (`update_payment_schedule` at L782-894), three new private helpers added to the same class. Net diff ≈ +25 lines. No public API change, no schema change, no new imports.

**Pedagogical role:** warm-up of a 3-part didactical refactoring series. Lessons: extract-method (Finding 2 of `docs/reviews/2026-05-27-erpnext-architecture-review-1.md`) + batch-the-N+1 (Finding 3, line 795). The conversion_rate per-iteration query at line 832 — also an N+1, not in the original finding list — is folded in by user request as a second batching demonstration.

## Goal

Replace the 114-line braided body of `update_payment_schedule` with a ~20-line orchestrator that delegates to three single-concern private helpers. While doing so, replace two per-iteration database calls (Payment Schedule fetch at L795; per-reference `conversion_rate` fetch at L832) with batched queries issued once before the loop. Behavior preserved exactly.

## Out of scope

- Unifying the cancel/apply SQL UPDATE branches via a sign parameter (considered as Approach A, dropped per YAGNI — not in the named findings).
- Hoisting the `frappe.get_meta("Payment Schedule").get_field(...)` precision lookups out of the loop (metadata lookup, Frappe-cached, not a DB call).
- Any change to `validate_allocated_amount_with_latest_data` at L417 (the validate-phase over-allocation check that mirrors the persist-time check at L867-872).

## Target shape

### Orchestrator — `update_payment_schedule(self, cancel=0)`

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
```

### Helper 1 — `_build_payment_term_maps(self)`

Returns `(payment_amount_map, schedule_detail_map, conversion_rate_map)`.

Pass 1 — iterate `self.get("references")`. For each ref with both `payment_term` and `reference_name` set:
- `key = (ref.payment_term, ref.reference_name, ref.reference_doctype)`
- `payment_amount_map[key] += ref.allocated_amount`
- `ref_total_amount.setdefault((ref.reference_name, ref.reference_doctype), ref.total_amount)` (first-wins, matches the original's "skip fetch if key seen" semantics)
- `parent_set.add(ref.reference_name)`
- `doctype_to_names.setdefault(ref.reference_doctype, set()).add(ref.reference_name)`

Early return `({}, {}, {})` if `parent_set` empty.

Pass 2 — one batched Payment Schedule query, filtered by parent only (matches the original's per-parent filter at L797):

```python
PS = frappe.qb.DocType("Payment Schedule")
ps_rows = (
    frappe.qb.from_(PS)
    .select(PS.parent, PS.payment_term, PS.outstanding, PS.discount, PS.discount_type)
    .where(PS.parent.isin(list(parent_set)))
).run(as_dict=True)
```

Pass 3 — batched `conversion_rate` queries, one per distinct reference_doctype. Typically 1-2 doctypes in practice (Sales Invoice / Purchase Invoice), at most 5-6 (adds Sales Order / Purchase Order / Dunning / Payment Entry):

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

Pass 4 — build `schedule_detail_map` by joining `ps_rows` with `doctype_to_names`:

```python
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
```

Note: the original's filter at L797 is `{"parent": ref.reference_name}` with no `parenttype` constraint, so Payment Schedule rows from other parenttypes that happen to share the parent name are included. The refactor matches this exactly (no `parenttype` filter on the qb query) — preserves an observable quirk; not introducing one.

### Helper 2 — `_compute_base_amounts(self, allocated_amount, schedule_entry, conversion_rate)`

Returns `(base_paid_amount, base_outstanding, discounted_amt, outstanding)`. No DB calls — `conversion_rate` is passed in by the orchestrator. Arithmetic identical to L829-843:

```python
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
```

### Helper 3 — `_apply_payment_schedule_update(self, idx, key, allocated_amount, discounted_amt, base_paid_amount, base_outstanding, outstanding, cancel)`

Houses the cancel/apply branch from L845-894 verbatim. Two distinct raw SQL UPDATE statements, sign-flipped. Includes the over-allocation `frappe.throw` (L867-872) and the `if allocated_amount and outstanding` guard (L874) on the apply path. Not unified per YAGNI.

## Behavior-preservation invariants

The refactored code must satisfy all of:

1. Two raw SQL UPDATE statements against `tabPayment Schedule` are byte-identical to L846-865 (cancel branch) and L875-894 (apply branch) — same columns, same parameter order, same sign of operations.
2. `frappe.throw(_("Payment term {0} not used in {1}"))` fires under identical conditions: any key in the aggregated `payment_amount_map` that has no entry in `schedule_detail_map`.
3. `frappe.throw(_("Row #{0}: Cannot allocate more than {1} against payment term {2}"))` fires under identical conditions: apply path only, `allocated_amount > outstanding`. `idx` uses 1-based numbering matching the original `enumerate(..., 1)`.
4. `discounted_amt` computed per key uses the FIRST `ref.total_amount` encountered for that `(reference_name, reference_doctype)` — matches original's "skip fetch if `invoice_paid_amount_map.get(key)`" semantics via `ref_total_amount.setdefault(...)`.
5. The per-parent (not per-parent+parenttype) fetch semantics are preserved.
6. `conversion_rate` value is identical to what `frappe.db.get_value(doctype, {"name": name}, "conversion_rate")` returns for the same `(doctype, name)` — batched `get_all` with a name list and a single explicit field has the same semantics for this lookup.
7. Iteration order over `payment_amount_map.items()` matches the order keys are first inserted in pass 1 (Python ≥ 3.7 dict insertion-order guarantee). The `idx` in the over-allocation error message therefore matches the original.

## Query count, before/after

| Path | Before | After |
|---|---|---|
| Payment Schedule fetches | 1 per unique `reference_name` | 1 (batched) |
| `conversion_rate` fetches | 1 per aggregated key | 1 per distinct `reference_doctype` (typically 1-2) |
| SQL UPDATEs to `tabPayment Schedule` | 1 per aggregated key | 1 per aggregated key (unchanged — these are the actual writes) |

No performance assertions in the spec; performance comparison was explicitly out of scope for this warm-up.

## Verified assumptions

| # | Assumption | Evidence |
|---|---|---|
| A1 | Payment Schedule has the columns the SQL UPDATEs touch | `erpnext/accounts/doctype/payment_schedule/payment_schedule.json` — paid_amount, base_paid_amount, discounted_amount, outstanding, base_outstanding, payment_term, discount, discount_type all present; `istable: 1` so parent/parenttype are implicit |
| A2 | Payment Entry Reference has the needed fields | `erpnext/accounts/doctype/payment_entry_reference/payment_entry_reference.json` — reference_doctype, reference_name, payment_term, allocated_amount, total_amount all present |
| A3 | All invoice-class reference doctypes have `conversion_rate` | Sales Invoice ✓, Purchase Invoice ✓, Sales Order ✓, Purchase Order ✓, Dunning ✓, Payment Entry ✓. Journal Entry does NOT — but refs with `payment_term` are filtered to invoice-class doctypes in practice; original code has the same implicit constraint |
| A4 | `frappe.qb` batched-query pattern works in this codebase | Same-file precedent at payment_entry.py:2181-2192; `.parent.isin([...])` at accounts/utils.py:606, 2009, 2345, 2347 |
| A5 | `frappe.db.get_all(doctype, filters={"name": ("in", list)}, fields=[...])` returns dot-accessible rows | Precedents: purchase_invoice.py:1924, selling_controller.py:97, 265, 784, buying_controller.py:149, 1217 |
| A6 | Over-allocation test exercises persist-time throw | NO — test uses `pe.save()` (validate phase); persist-time throw at L867-872 is a defensive safety net, preserved byte-for-byte |
| A7 | No hooks intercept the method | hooks.py: zero refs; external grep: zero refs |
| A8 | Only two call sites, no dynamic dispatch | payment_entry.py:207 (`on_submit`), :312 (`on_cancel`, cancel=1); no getattr/string-name calls |
| A9 | No test mocks the internals | grep of test_payment_entry.py: no monkeypatch/mock/patch on this method or its DB calls |
| A10 | Empty-input guards are sufficient | helper early-returns `({}, {}, {})` when `parent_set` empty; orchestrator's for-loop is a no-op on an empty `payment_amount_map`, so the other two maps are never indexed |

## Known issues, accepted as out of scope

- The persist-time over-allocation throw at L867-872 (preserved verbatim in `_apply_payment_schedule_update`) is not directly exercised by the test suite. The validate-phase check at L417 catches the same condition under normal flow; the persist-time throw fires only on state-change-between-validate-and-submit. CI provides no positive signal on its preservation, but the byte-for-byte copy makes regression unlikely.

## Verification strategy

No local Frappe Bench in this environment. Verification via CI: push branch → `.github/workflows/server-tests-mariadb.yml` runs `bench --site test_site run-parallel-tests --app erpnext` against MariaDB. The 2271-line `test_payment_entry.py` is the contract; relevant tests include `test_overallocation_validation_on_payment_terms` (L1190) and the payment-schedule-affecting tests at L300-450.
