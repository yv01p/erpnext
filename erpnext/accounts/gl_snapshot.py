"""Golden-master snapshot harness for GL Entry characterization tests.

Captures the General Ledger entries produced by a submitted voucher in a
normalized, deterministic form and compares them against a stored golden
snapshot. Volatile fields (name, creation, voucher number) are stripped so the
snapshot is stable across runs.

This is the Phase 0 safety net for the accounts/controller refactor: every
later phase must keep these snapshots byte-identical. Regenerate goldens with::

    REGEN_GL_SNAPSHOTS=1 bench run-tests --site test-site-ai \\
        --module erpnext.accounts.test_gl_characterization
"""

import json
import os
from pathlib import Path

import frappe
from frappe.utils import flt

SNAPSHOT_DIR = Path(__file__).parent / "gl_snapshots"
REGEN_ENV = "REGEN_GL_SNAPSHOTS"
PRECISION = 2


class GLSnapshot:
	"""Normalized, order-stable view of a voucher's GL entries."""

	def __init__(self, voucher_type: str, voucher_no: str) -> None:
		self.voucher_type = voucher_type
		self.voucher_no = voucher_no

	def capture(self) -> list[dict]:
		rows = [self._normalize(row) for row in self._fetch_rows()]
		# Sort on the full normalized row so ordering never depends on the DB's
		# return order — e.g. two POS payment legs that tie on account/party/amount
		# but differ only in `against`.
		return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))

	def _fetch_rows(self) -> list[dict]:
		gl = frappe.qb.DocType("GL Entry")
		query = (
			frappe.qb.from_(gl)
			.select(
				gl.account,
				gl.party_type,
				gl.party,
				gl.debit,
				gl.credit,
				gl.debit_in_account_currency,
				gl.credit_in_account_currency,
				gl.account_currency,
				gl.against,
				gl.cost_center,
				gl.is_opening,
				gl.posting_date,
			)
			.where(
				(gl.voucher_type == self.voucher_type)
				& (gl.voucher_no == self.voucher_no)
				& (gl.is_cancelled == 0)
			)
			.orderby(gl.account, gl.party, gl.debit, gl.credit)
		)
		return query.run(as_dict=True)

	def _normalize(self, row: dict) -> dict:
		return {
			"account": row.account,
			"party_type": row.party_type or None,
			"party": row.party or None,
			"debit": flt(row.debit, PRECISION),
			"credit": flt(row.credit, PRECISION),
			"debit_in_account_currency": flt(row.debit_in_account_currency, PRECISION),
			"credit_in_account_currency": flt(row.credit_in_account_currency, PRECISION),
			"account_currency": row.account_currency,
			"against": self._normalize_against(row.against),
			"cost_center": row.cost_center,
			"is_opening": row.is_opening,
			"posting_date": str(row.posting_date),
		}

	def _normalize_against(self, against: str | None) -> str | None:
		"""`against` is a comma-joined account list whose order is not stable."""
		if not against:
			return None
		return ", ".join(sorted(part.strip() for part in against.split(",")))


def assert_gl_snapshot(test_case, name: str, voucher_type: str, voucher_no: str) -> None:
	"""Compare a voucher's GL entries against the golden snapshot ``name``.

	In regen mode (``REGEN_GL_SNAPSHOTS`` set) the golden file is written instead
	of asserted, so the same scenarios both produce and verify the goldens.
	"""
	actual = GLSnapshot(voucher_type, voucher_no).capture()
	path = SNAPSHOT_DIR / f"{name}.json"

	if os.environ.get(REGEN_ENV):
		SNAPSHOT_DIR.mkdir(exist_ok=True)
		path.write_text(json.dumps(actual, indent="\t", sort_keys=True) + "\n")
		return

	test_case.assertTrue(
		path.exists(),
		f"Golden snapshot {path} missing. Run with {REGEN_ENV}=1 to create it.",
	)
	expected = json.loads(path.read_text())
	test_case.assertEqual(expected, actual, f"GL snapshot mismatch for '{name}'")
