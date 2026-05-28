# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import flt


def get_item_details(items):
	item = frappe.qb.DocType("Item")
	item_list = (
		frappe.qb.from_(item)
		.select(item.item_code, item.item_name, item.description, item.allow_alternative_item)
		.where(item.name.isin(items))
		.run(as_dict=True)
	)

	item_details = {}
	for item in item_list:
		item_details[item.item_code] = item

	return item_details


def get_pending_subcontracted_quantity(doctype, name):
	table = frappe.qb.DocType(doctype)
	query = (
		frappe.qb.from_(table)
		.select(table.name, table.stock_qty, table.subcontracted_qty)
		.where(table.parent == name)
	)
	return {item.name: item.stock_qty - item.subcontracted_qty for item in query.run(as_dict=True)}


@frappe.whitelist()
def make_rm_stock_entry(
	subcontract_order, rm_items=None, order_doctype="Subcontracting Order", target_doc=None
):
	if subcontract_order:
		subcontract_order = frappe.get_doc(order_doctype, subcontract_order)

		if not rm_items:
			if not subcontract_order.supplied_items:
				frappe.throw(_("No item available for transfer."))

			rm_items = subcontract_order.supplied_items

		fg_item_code_list = list(
			set(item.get("main_item_code") or item.get("item_code") for item in rm_items)
		)

		if fg_item_code_list:
			rm_item_code_list = tuple(set(item.get("rm_item_code") for item in rm_items))
			item_wh = get_item_details(rm_item_code_list)

			field_no_map, rm_detail_field = "purchase_order", "sco_rm_detail"
			if order_doctype == "Purchase Order":
				field_no_map, rm_detail_field = "subcontracting_order", "po_detail"

			if target_doc and target_doc.get("items"):
				target_doc.items = []

			def post_process(source_doc, target_doc):
				target_doc.purpose = "Send to Subcontractor"

				if order_doctype == "Purchase Order":
					target_doc.purchase_order = source_doc.name
				else:
					target_doc.subcontracting_order = source_doc.name

				target_doc.set_stock_entry_type()

				over_transfer_allowance = frappe.get_single_value(
					"Buying Settings", "over_transfer_allowance"
				)
				for fg_item_code in fg_item_code_list:
					for rm_item in rm_items:
						if (
							rm_item.get("main_item_code") == fg_item_code
							or rm_item.get("item_code") == fg_item_code
						):
							rm_item_code = rm_item.get("rm_item_code")
							qty = rm_item.get("qty") or max(
								rm_item.get("required_qty") - rm_item.get("total_supplied_qty"), 0
							)
							if qty <= 0 and rm_item.get("total_supplied_qty"):
								per_transferred = (
									flt(
										rm_item.get("total_supplied_qty") / rm_item.get("required_qty"),
										frappe.db.get_default("float_precision"),
									)
									* 100
								)
								if per_transferred >= 100 + over_transfer_allowance:
									continue

							items_dict = {
								rm_item_code: {
									rm_detail_field: rm_item.get("name"),
									"item_name": rm_item.get("item_name")
									or item_wh.get(rm_item_code, {}).get("item_name", ""),
									"description": item_wh.get(rm_item_code, {}).get("description", ""),
									"qty": qty,
									"from_warehouse": rm_item.get("warehouse")
									or rm_item.get("reserve_warehouse"),
									"to_warehouse": source_doc.supplier_warehouse,
									"stock_uom": rm_item.get("stock_uom"),
									"serial_and_batch_bundle": rm_item.get("serial_and_batch_bundle"),
									"main_item_code": fg_item_code,
									"allow_alternative_item": item_wh.get(rm_item_code, {}).get(
										"allow_alternative_item"
									),
									"use_serial_batch_fields": rm_item.get("use_serial_batch_fields"),
									"serial_no": rm_item.get("serial_no")
									if rm_item.get("use_serial_batch_fields")
									else None,
									"batch_no": rm_item.get("batch_no")
									if rm_item.get("use_serial_batch_fields")
									else None,
								}
							}

							target_doc.add_to_stock_entry_detail(items_dict)

			stock_entry = get_mapped_doc(
				order_doctype,
				subcontract_order.name,
				{
					order_doctype: {
						"doctype": "Stock Entry",
						"field_map": {
							"supplier": "supplier",
							"supplier_name": "supplier_name",
							"supplier_address": "supplier_address",
							"to_warehouse": "supplier_warehouse",
						},
						"field_no_map": [field_no_map],
						"validation": {
							"docstatus": ["=", 1],
						},
					},
				},
				target_doc,
				ignore_child_tables=True,
				postprocess=post_process,
			)

			if target_doc:
				return stock_entry
			else:
				return stock_entry.as_dict()
		else:
			frappe.throw(_("No Items selected for transfer."))


def add_items_in_ste(ste_doc, row, qty, rm_details, rm_detail_field="sco_rm_detail", batch_no=None):
	item = ste_doc.append("items", row.item_details)

	rm_detail = list(set(row.get(f"{rm_detail_field}s")).intersection(rm_details))
	item.update(
		{
			"qty": qty,
			"batch_no": batch_no,
			"basic_rate": row.item_details["rate"],
			rm_detail_field: rm_detail[0] if rm_detail else "",
			"s_warehouse": row.item_details["t_warehouse"],
			"t_warehouse": row.item_details["s_warehouse"],
			"item_code": row.item_details["rm_item_code"],
			"subcontracted_item": row.item_details["main_item_code"],
			"serial_no": "\n".join(row.serial_no) if row.serial_no else "",
			"use_serial_batch_fields": 1,
		}
	)


def make_return_stock_entry_for_subcontract(
	available_materials, order_doc, rm_details, order_doctype="Subcontracting Order"
):
	rm_detail_field = "po_detail" if order_doctype == "Purchase Order" else "sco_rm_detail"

	def post_process(source_doc, target_doc):
		target_doc.purpose = "Material Transfer"

		if source_doc.doctype == "Purchase Order":
			target_doc.purchase_order = source_doc.name
		else:
			target_doc.subcontracting_order = source_doc.name

		target_doc.company = source_doc.company
		target_doc.is_return = 1
		for _key, value in available_materials.items():
			if not value.qty:
				continue

			if item_details := value.get("item_details"):
				item_details["serial_and_batch_bundle"] = None

			if value.batch_no:
				for batch_no, qty in value.batch_no.items():
					if qty > 0:
						add_items_in_ste(target_doc, value, qty, rm_details, rm_detail_field, batch_no)
			else:
				add_items_in_ste(target_doc, value, value.qty, rm_details, rm_detail_field)

		target_doc.set_stock_entry_type()

	ste_doc = get_mapped_doc(
		order_doctype,
		order_doc.name,
		{
			order_doctype: {
				"doctype": "Stock Entry",
				"field_no_map": ["purchase_order", "subcontracting_order"],
			},
		},
		ignore_child_tables=True,
		postprocess=post_process,
	)

	return ste_doc


@frappe.whitelist()
def get_materials_from_supplier(subcontract_order, rm_details, order_doctype="Subcontracting Order"):
	if isinstance(rm_details, str):
		rm_details = json.loads(rm_details)

	doc = frappe.get_cached_doc(order_doctype, subcontract_order)
	doc.initialized_fields()
	doc.subcontract_orders = [doc.name]
	doc.get_available_materials()

	if not doc.available_materials:
		frappe.throw(
			_("Materials are already received against the {0} {1}").format(order_doctype, subcontract_order)
		)

	return make_return_stock_entry_for_subcontract(doc.available_materials, doc, rm_details, order_doctype)
