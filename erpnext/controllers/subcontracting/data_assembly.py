import copy
from collections import defaultdict

import frappe

from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
	get_voucher_wise_serial_batch_from_bundle,
)
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos


def _get_data_before_save(doc):
	item_dict = {}
	if (
		doc.doctype in ["Purchase Receipt", "Purchase Invoice", "Subcontracting Receipt"]
		and doc._doc_before_save
	):
		for row in doc._doc_before_save.get("items"):
			item_dict[row.name] = (row.item_code, row.received_qty)

	return item_dict


def _identify_change_in_item_table(doc):
	doc._changed_name = []
	doc._reference_name = []

	if (
		doc.doctype in ["Purchase Order", "Subcontracting Order", "Subcontracting Inward Order"]
		or doc.is_new()
	):
		doc.set(doc.raw_material_table, [])
		return

	if not doc.get(doc.raw_material_table):
		return

	item_dict = _get_data_before_save(doc)
	if not item_dict:
		return True

	for row in doc.items:
		doc._reference_name.append(row.name)
		if (row.name not in item_dict) or (
			row.item_code,
			row.received_qty,
		) != item_dict[row.name]:
			doc._changed_name.append(row.name)

		if item_dict.get(row.name):
			del item_dict[row.name]

	doc._changed_name.extend(item_dict.keys())


def _get_backflush_based_on(doc):
	doc.backflush_based_on = (
		frappe.db.get_single_value(
			"Buying Settings",
			"backflush_raw_materials_of_subcontract_based_on",
		)
		if doc.subcontract_data.order_doctype == "Subcontracting Order"
		else "Material Transferred for Subcontract"
	)


def initialized_fields(doc):
	doc.available_materials = frappe._dict()
	doc._transferred_items = frappe._dict()
	doc.alternative_item_details = frappe._dict()
	_get_backflush_based_on(doc)


def _get_subcontract_orders(doc):
	doc.subcontract_orders = []

	if doc.doctype in ["Purchase Order", "Subcontracting Order", "Subcontracting Inward Order"]:
		return

	doc.subcontract_orders = [
		item.get(doc.subcontract_data.order_field)
		for item in doc.items
		if item.get(doc.subcontract_data.order_field)
	]


def _get_pending_qty_to_receive(doc):
	"""Get qty to be received against the subcontract order."""

	doc.qty_to_be_received = defaultdict(float)

	if (
		doc.doctype != doc.subcontract_data.order_doctype
		and doc.backflush_based_on != "BOM"
		and doc.subcontract_orders
	):
		for row in frappe.get_all(
			f"{doc.subcontract_data.order_doctype} Item",
			fields=["item_code", {"SUB": ["qty", "received_qty"], "as": "qty"}, "parent", "bom"],
			filters={"docstatus": 1, "parent": ("in", doc.subcontract_orders)},
		):
			doc.qty_to_be_received[(row.item_code, row.parent, row.bom)] += row.qty


def _get_transferred_items(doc):
	se = frappe.qb.DocType("Stock Entry")
	se_detail = frappe.qb.DocType("Stock Entry Detail")

	query = (
		frappe.qb.from_(se)
		.inner_join(se_detail)
		.on(se.name == se_detail.parent)
		.select(
			se[doc.subcontract_data.order_field],
			se.name.as_("voucher_no"),
			se_detail.item_code.as_("rm_item_code"),
			se_detail.item_name,
			se_detail.description,
			(
				frappe.qb.terms.Case()
				.when(((se.purpose == "Material Transfer") & (se.is_return == 1)), -1 * se_detail.qty)
				.else_(se_detail.qty)
			).as_("qty"),
			se_detail.basic_rate.as_("rate"),
			se_detail.amount,
			se_detail.serial_no,
			se_detail.serial_and_batch_bundle,
			se_detail.uom,
			se_detail.subcontracted_item.as_("main_item_code"),
			se_detail.stock_uom,
			se_detail.batch_no,
			se_detail.conversion_factor,
			se_detail.s_warehouse,
			se_detail.t_warehouse,
			se_detail.item_group,
			se_detail[doc.subcontract_data.rm_detail_field],
		)
		.where(
			(se.docstatus == 1)
			& (se[doc.subcontract_data.order_field].isin(doc.subcontract_orders))
			& (
				(se.purpose == "Send to Subcontractor")
				| ((se.purpose == "Material Transfer") & (se.is_return == 1))
			)
		)
	)

	if doc.backflush_based_on == "BOM":
		query = query.select(se_detail.original_item)

	return query.run(as_dict=True)


def _set_alternative_item_details(doc, row):
	if row.get("original_item"):
		doc.alternative_item_details[row.get("original_item")] = row


def _get_received_items(doc, doctype):
	fields = []
	for field in ["name", doc.subcontract_data.order_field, "parent"]:
		fields.append(f"`tab{doctype} Item`.`{field}`")

	filters = [
		[doctype, "docstatus", "=", 1],
		[f"{doctype} Item", doc.subcontract_data.order_field, "in", doc.subcontract_orders],
	]
	if doctype == "Purchase Invoice":
		filters.append(["Purchase Invoice", "update_stock", "=", 1])

	return frappe.get_all(f"{doctype}", fields=fields, filters=filters)


def _get_consumed_items(doc, doctype, receipt_items):
	fields = [
		"serial_no",
		"rm_item_code",
		"reference_name",
		"batch_no",
		"consumed_qty",
		"main_item_code",
		"parent as voucher_no",
	]

	if doc.subcontract_data.receipt_supplied_items_field != "Purchase Receipt Item Supplied":
		fields.append("serial_and_batch_bundle")

	return frappe.get_all(
		doc.subcontract_data.receipt_supplied_items_field,
		fields=fields,
		filters={"docstatus": 1, "reference_name": ("in", list(receipt_items)), "parenttype": doctype},
	)


def _update_consumed_materials(doc, doctype, return_consumed_items=False):
	"""Deduct the consumed materials from the available materials."""

	receipt_items = _get_received_items(doc, doctype)
	if not receipt_items:
		return ([], {}) if return_consumed_items else None

	receipt_items = {item.name: item.get(doc.subcontract_data.order_field) for item in receipt_items}
	consumed_materials = _get_consumed_items(doc, doctype, receipt_items.keys())

	if return_consumed_items:
		return (consumed_materials, receipt_items)

	if not consumed_materials:
		return

	voucher_nos = [d.voucher_no for d in consumed_materials if d.voucher_no]
	voucher_bundle_data = (
		get_voucher_wise_serial_batch_from_bundle(
			voucher_no=voucher_nos,
			is_outward=1,
			get_subcontracted_item=("Subcontracting Receipt Supplied Item", "main_item_code"),
		)
		if voucher_nos
		else {}
	)

	for row in consumed_materials:
		key = (row.rm_item_code, row.main_item_code, receipt_items.get(row.reference_name))
		if not doc.available_materials.get(key):
			continue

		doc.available_materials[key]["qty"] -= row.consumed_qty

		bundle_key = (row.rm_item_code, row.main_item_code, doc.supplier_warehouse, row.voucher_no)
		consumed_bundles = voucher_bundle_data.get(bundle_key, frappe._dict())

		if consumed_bundles.serial_nos:
			doc.available_materials[key]["serial_no"] = list(
				set(doc.available_materials[key]["serial_no"]) - set(consumed_bundles.serial_nos)
			)

		if consumed_bundles.batch_nos:
			for batch_no, qty in consumed_bundles.batch_nos.items():
				if qty:
					# Conumed qty is negative therefore added it instead of subtracting
					doc.available_materials[key]["batch_no"][batch_no] += qty
					consumed_bundles.batch_nos[batch_no] += abs(qty)

		# Will be deprecated in v16
		if row.serial_no and not consumed_bundles.serial_nos:
			from erpnext.deprecation_dumpster import deprecation_warning

			deprecation_warning("unknown", "v16", "No instructions.")
			doc.available_materials[key]["serial_no"] = list(
				set(doc.available_materials[key]["serial_no"]) - set(get_serial_nos(row.serial_no))
			)

		# Will be deprecated in v16
		if row.batch_no and not consumed_bundles.batch_nos:
			from erpnext.deprecation_dumpster import deprecation_warning

			deprecation_warning("unknown", "v16", "No instructions.")
			doc.available_materials[key]["batch_no"][row.batch_no] -= row.consumed_qty


def get_available_materials(doc):
	"""Get the available raw materials which has been transferred to the supplier.
	available_materials = {
	        (item_code, subcontracted_item, subcontract_order): {
	                'qty': 1, 'serial_no': [ABC], 'batch_no': {'batch1': 1}, 'data': item_details
	        }
	}
	"""
	if not doc.subcontract_orders:
		return

	transferred_items = _get_transferred_items(doc)

	voucher_nos = [row.voucher_no for row in transferred_items]
	voucher_bundle_data = (
		get_voucher_wise_serial_batch_from_bundle(
			voucher_no=voucher_nos,
			is_outward=0,
			get_subcontracted_item=("Stock Entry Detail", "subcontracted_item"),
		)
		if voucher_nos
		else {}
	)

	for row in transferred_items:
		key = (row.rm_item_code, row.main_item_code, row.get(doc.subcontract_data.order_field))

		if key not in doc.available_materials:
			doc.available_materials.setdefault(
				key,
				frappe._dict(
					{
						"qty": 0,
						"serial_no": [],
						"batch_no": defaultdict(float),
						"item_details": row,
						f"{doc.subcontract_data.rm_detail_field}s": [],
					}
				),
			)

		details = doc.available_materials[key]
		details.qty += row.qty
		details[f"{doc.subcontract_data.rm_detail_field}s"].append(
			row.get(doc.subcontract_data.rm_detail_field)
		)

		if row.serial_no:
			details.serial_no.extend(get_serial_nos(row.serial_no))
		if row.batch_no:
			details.batch_no[row.batch_no] += row.qty

		if not row.serial_no and not row.batch_no and voucher_bundle_data:
			bundle_key = (row.rm_item_code, row.main_item_code, row.t_warehouse, row.voucher_no)

			bundle_data = voucher_bundle_data.get(bundle_key, frappe._dict())
			if bundle_data.serial_nos:
				details.serial_no.extend(bundle_data.serial_nos)
				bundle_data.serial_nos = []

			if bundle_data.batch_nos:
				for batch_no, qty in bundle_data.batch_nos.items():
					if qty < 0:
						qty = abs(qty)

					if qty > 0:
						details.batch_no[batch_no] += qty
						bundle_data.batch_nos[batch_no] -= qty

		_set_alternative_item_details(doc, row)

	doc._transferred_items = copy.deepcopy(doc.available_materials)
	if doc.get("is_old_subcontracting_flow"):
		for doctype in ["Purchase Receipt", "Purchase Invoice"]:
			_update_consumed_materials(doc, doctype)
	else:
		_update_consumed_materials(doc, "Subcontracting Receipt")
