# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, get_link_to_form


def validate_rejected_warehouse(doc):
	for item in doc.get("items"):
		if flt(item.rejected_qty) and not item.rejected_warehouse:
			if doc.rejected_warehouse:
				item.rejected_warehouse = doc.rejected_warehouse
			else:
				frappe.throw(
					_("Row #{0}: Rejected Warehouse is mandatory for the rejected Item {1}").format(
						item.idx, item.item_code
					)
				)

		if item.get("rejected_warehouse") and (item.get("rejected_warehouse") == item.get("warehouse")):
			frappe.throw(
				_("Row #{0}: Accepted Warehouse and Rejected Warehouse cannot be same").format(item.idx)
			)


def remove_empty_rows(doc):
	for key in ["service_items", "items", "supplied_items", "received_items"]:
		if doc.get(key):
			idx = 1
			for item in doc.get(key)[:]:
				if not (item.get("item_code") or item.get("main_item_code")):
					doc.get(key).remove(item)
				else:
					item.idx = idx
					idx += 1


def set_items_conversion_factor(doc):
	for item in doc.get("items"):
		if not item.conversion_factor:
			item.conversion_factor = 1


def validate_items(doc):
	for item in doc.items:
		is_stock_item, is_sub_contracted_item = frappe.get_value(
			"Item", item.item_code, ["is_stock_item", "is_sub_contracted_item"]
		)

		if not is_stock_item:
			frappe.throw(_("Row {0}: Item {1} must be a stock item.").format(item.idx, item.item_name))

		if (
			doc.doctype == "Subcontracting Inward Order"
			and item.delivery_warehouse == doc.customer_warehouse
		):
			frappe.throw(
				_(
					"Row {0}: Delivery Warehouse cannot be same as Customer Warehouse for Item {1}."
				).format(item.idx, get_link_to_form("Item", item.item_code))
			)

		if not item.get("type") and not item.get("is_legacy_scrap_item"):
			if not is_sub_contracted_item:
				frappe.throw(
					_("Row {0}: Item {1} must be a subcontracted item.").format(item.idx, item.item_name)
				)

			if doc.doctype != "Subcontracting Receipt":
				order_item_doctype = (
					"Purchase Order Item"
					if doc.doctype == "Subcontracting Order"
					else "Sales Order Item"
				)

				order_name = (
					doc.purchase_order if doc.doctype == "Subcontracting Order" else doc.sales_order
				)
				order_item_field = frappe.scrub(order_item_doctype)

				if not item.get(order_item_field):
					frappe.throw(
						_("Row {0}: Item {1} must be linked to a {2}.").format(
							item.idx, item.item_name, order_item_doctype
						)
					)

				# IMPORTANT: at T1 this import points at the still-module-level function in
				# subcontracting_controller.py. T4 retargets it to `from .api import ...`.
				# Lazy import to avoid circular dependency.
				from erpnext.controllers.subcontracting_controller import get_pending_subcontracted_quantity

				pending_qty = flt(
					flt(
						get_pending_subcontracted_quantity(
							order_item_doctype,
							order_name,
						).get(item.get(order_item_field))
					)
					/ item.subcontracting_conversion_factor,
					frappe.get_precision(
						order_item_doctype,
						"qty",
					),
				)

				if item.qty > pending_qty:
					frappe.throw(
						_(
							"Row {0}: Item {1}'s quantity cannot be higher than the available quantity."
						).format(item.idx, item.item_name)
					)

			if doc.doctype not in ["Subcontracting Inward Order", "Subcontracting Receipt"]:
				item.amount = item.qty * item.rate

			if item.bom:
				is_active, bom_item = frappe.get_value("BOM", item.bom, ["is_active", "item"])

				if not is_active:
					frappe.throw(
						_("Row {0}: Please select an active BOM for Item {1}.").format(
							item.idx, item.item_name
						)
					)
				if bom_item != item.item_code:
					frappe.throw(
						_("Row {0}: Please select an valid BOM for Item {1}.").format(
							item.idx, item.item_name
						)
					)
			else:
				frappe.throw(
					_("Row {0}: Please select a BOM for Item {1}.").format(item.idx, item.item_name)
				)
		else:
			item.bom = None
