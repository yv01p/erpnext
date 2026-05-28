# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.utils import cint, flt

from erpnext.controllers.stock_controller import StockController
from erpnext.controllers.subcontracting import data_assembly, validation
from erpnext.controllers.subcontracting.supplied_items import SuppliedItemsHelper
from erpnext.stock.utils import get_incoming_rate


class SubcontractingController(StockController):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		if self.get("is_old_subcontracting_flow"):
			self.subcontract_data = frappe._dict(
				{
					"order_doctype": "Purchase Order",
					"order_field": "purchase_order",
					"rm_detail_field": "po_detail",
					"receipt_supplied_items_field": "Purchase Receipt Item Supplied",
					"order_supplied_items_field": "Purchase Order Item Supplied",
				}
			)
		elif self.doctype == "Subcontracting Inward Order":
			self.subcontract_data = frappe._dict(
				{
					"order_doctype": "Subcontracting Inward Order",
					"order_field": "subcontracting_inward_order",
					"rm_detail_field": "scio_detail",
				}
			)
		else:
			self.subcontract_data = frappe._dict(
				{
					"order_doctype": "Subcontracting Order",
					"order_field": "subcontracting_order",
					"rm_detail_field": "sco_rm_detail",
					"receipt_supplied_items_field": "Subcontracting Receipt Supplied Item",
					"order_supplied_items_field": "Subcontracting Order Supplied Item",
				}
			)
		self.supplied_items_helper = SuppliedItemsHelper(self)

	def before_validate(self):
		if self.doctype in [
			"Subcontracting Order",
			"Subcontracting Inward Order",
			"Subcontracting Receipt",
		]:
			self.remove_empty_rows()
			self.set_items_conversion_factor()

	def validate(self):
		if self.doctype in ["Subcontracting Order", "Subcontracting Receipt", "Subcontracting Inward Order"]:
			self.validate_items()
			self.create_raw_materials_supplied_or_received(
				raw_material_table="supplied_items"
				if self.doctype != "Subcontracting Inward Order"
				else "received_items"
			)
			self.set_valuation_rate_for_rm()
		else:
			super().validate()

	def set_valuation_rate_for_rm(self):
		self.supplied_items_helper.set_valuation_rate_for_rm()

	def validate_rejected_warehouse(self):
		validation.validate_rejected_warehouse(self)

	def remove_empty_rows(self):
		validation.remove_empty_rows(self)

	def set_items_conversion_factor(self):
		validation.set_items_conversion_factor(self)

	def validate_items(self):
		validation.validate_items(self)

	def initialized_fields(self):
		data_assembly.initialized_fields(self)

	def get_available_materials(self):
		data_assembly.get_available_materials(self)

	def _get_materials_from_bom(self, item_code, bom_no, exploded_item=0):
		return self.supplied_items_helper._get_materials_from_bom(item_code, bom_no, exploded_item)

	def set_batch_for_supplied_items(self):
		self.supplied_items_helper.set_batch_for_supplied_items()

	def batch_has_not_available(self, batch_no, qty_required):
		return self.supplied_items_helper.batch_has_not_available(batch_no, qty_required)

	def update_rate_for_supplied_items(self):
		self.supplied_items_helper.update_rate_for_supplied_items()

	def get_item_row(self, reference_name):
		return self.supplied_items_helper.get_item_row(reference_name)

	def set_rate_for_supplied_items(self, rm_obj, item_row):
		self.supplied_items_helper.set_rate_for_supplied_items(rm_obj, item_row)

	def set_materials_for_subcontracted_items(self, raw_material_table):
		if self.doctype == "Purchase Invoice" and not self.update_stock:
			return

		self.raw_material_table = raw_material_table
		data_assembly._identify_change_in_item_table(self)
		self.supplied_items_helper._prepare_supplied_or_received_items()
		self.supplied_items_helper._validate_supplied_or_received_items()

	def create_raw_materials_supplied_or_received(self, raw_material_table="supplied_items"):
		self.set_materials_for_subcontracted_items(raw_material_table)

		if self.doctype in ["Subcontracting Receipt", "Purchase Receipt", "Purchase Invoice"]:
			for item in self.get("items"):
				item.rm_supp_cost = 0.0

	def __update_consumed_qty_in_subcontract_order(self, itemwise_consumed_qty):
		fields = ["main_item_code", "rm_item_code", "parent", "supplied_qty", "name"]
		filters = {"docstatus": 1, "parent": ("in", self.subcontract_orders)}

		for row in frappe.get_all(
			self.subcontract_data.order_supplied_items_field, fields=fields, filters=filters, order_by="idx"
		):
			key = (row.rm_item_code, row.main_item_code, row.parent)
			consumed_qty = itemwise_consumed_qty.get(key, 0)

			if row.supplied_qty < consumed_qty:
				consumed_qty = row.supplied_qty

			itemwise_consumed_qty[key] -= consumed_qty
			frappe.db.set_value(
				self.subcontract_data.order_supplied_items_field, row.name, "consumed_qty", consumed_qty
			)

	def set_consumed_qty_in_subcontract_order(self):
		# Update consumed qty back in the subcontract order
		if self.doctype in ["Subcontracting Order", "Subcontracting Receipt"] or self.get(
			"is_old_subcontracting_flow"
		):
			data_assembly._get_subcontract_orders(self)
			itemwise_consumed_qty = defaultdict(float)
			if self.get("is_old_subcontracting_flow"):
				doctypes = ["Purchase Receipt", "Purchase Invoice"]
			else:
				doctypes = ["Subcontracting Receipt"]

			for doctype in doctypes:
				consumed_items, receipt_items = data_assembly._update_consumed_materials(
					self, doctype, return_consumed_items=True
				)

				for row in consumed_items:
					key = (row.rm_item_code, row.main_item_code, receipt_items.get(row.reference_name))
					itemwise_consumed_qty[key] += row.consumed_qty

			self.__update_consumed_qty_in_subcontract_order(itemwise_consumed_qty)

	def update_ordered_and_reserved_qty(self):
		sco_map = {}
		for item in self.get("items"):
			if self.doctype == "Subcontracting Receipt" and item.subcontracting_order:
				sco_map.setdefault(item.subcontracting_order, []).append(item.subcontracting_order_item)

		for sco, sco_item_rows in sco_map.items():
			if sco and sco_item_rows:
				sco_doc = frappe.get_doc("Subcontracting Order", sco)

				if sco_doc.status in ["Closed", "Cancelled"]:
					frappe.throw(
						_("{0} {1} is cancelled or closed").format(_("Subcontracting Order"), sco),
						frappe.InvalidStatusError,
					)

				sco_doc.update_ordered_qty_for_subcontracting(sco_item_rows)
				sco_doc.update_reserved_qty_for_subcontracting(sco_item_rows)

	def make_sl_entries_for_supplier_warehouse(self, sl_entries):
		if hasattr(self, "supplied_items"):
			for item in self.get("supplied_items"):
				# negative quantity is passed, as raw material qty has to be decreased
				# when SCR is submitted and it has to be increased when SCR is cancelled
				sl_entries.append(
					self.get_sl_entries(
						item,
						{
							"item_code": item.rm_item_code,
							"incoming_rate": item.rate if self.is_return else 0,
							"warehouse": self.supplier_warehouse,
							"actual_qty": -1 * flt(item.consumed_qty, item.precision("consumed_qty")),
							"dependant_sle_voucher_detail_no": item.reference_name,
						},
					)
				)

	def update_stock_ledger(self, allow_negative_stock=False, via_landed_cost_voucher=False):
		self.update_ordered_and_reserved_qty()

		sl_entries = []
		stock_items = self.get_stock_items()

		for item in self.get("items"):
			if item.item_code in stock_items and item.warehouse:
				scr_qty = flt(item.qty) * flt(item.conversion_factor)

				if scr_qty:
					sle = self.get_sl_entries(item, {"actual_qty": flt(scr_qty)})
					rate_db_precision = 6 if cint(self.precision("rate", item)) <= 6 else 9
					incoming_rate = flt(item.rate, rate_db_precision)
					sle.update(
						{
							"incoming_rate": incoming_rate,
							"recalculate_rate": 1,
						}
					)
					sl_entries.append(sle)

				if flt(item.rejected_qty) != 0:
					sl_entries.append(
						self.get_sl_entries(
							item,
							{
								"warehouse": item.rejected_warehouse,
								"serial_and_batch_bundle": item.get("rejected_serial_and_batch_bundle"),
								"actual_qty": flt(item.rejected_qty) * flt(item.conversion_factor),
								"incoming_rate": 0.0,
							},
						)
					)

		self.make_sl_entries_for_supplier_warehouse(sl_entries)
		self.make_sl_entries(
			sl_entries,
			allow_negative_stock=allow_negative_stock,
			via_landed_cost_voucher=via_landed_cost_voucher,
		)

	def get_supplied_items_cost(self, item_row_id, reset_outgoing_rate=True):
		supplied_items_cost = 0.0
		for item in self.get("supplied_items"):
			if item.reference_name == item_row_id:
				if (
					self.get("is_old_subcontracting_flow")
					and reset_outgoing_rate
					and frappe.get_cached_value("Item", item.rm_item_code, "is_stock_item")
				):
					rate = get_incoming_rate(
						{
							"item_code": item.rm_item_code,
							"warehouse": self.supplier_warehouse,
							"posting_date": self.posting_date,
							"posting_time": self.posting_time,
							"qty": -1 * item.consumed_qty,
							"voucher_detail_no": item.name,
							"serial_and_batch_bundle": item.get("serial_and_batch_bundle"),
							"serial_no": item.get("serial_no"),
							"batch_no": item.get("batch_no"),
						}
					)

					if rate > 0:
						item.rate = rate

				item.amount = flt(flt(item.consumed_qty) * flt(item.rate), item.precision("amount"))
				supplied_items_cost += item.amount

		return supplied_items_cost

	def set_subcontracting_order_status(self, update_bin=True):
		if self.doctype == "Subcontracting Order":
			self.update_status()
		elif self.doctype == "Subcontracting Receipt":
			if self.subcontract_orders:
				for sco in set(self.subcontract_orders):
					sco_doc = frappe.get_doc("Subcontracting Order", sco)
					sco_doc.update_status(update_bin=update_bin)

	def calculate_additional_costs(self):
		self.total_additional_costs = sum(flt(item.amount) for item in self.get("additional_costs"))

		if self.total_additional_costs:
			if self.distribute_additional_costs_based_on == "Amount":
				total_amt = sum(
					flt(item.amount)
					for item in self.get("items")
					if not item.get("type") and not item.get("is_legacy_scrap_item")
				)
				for item in self.items:
					if not item.get("type") and not item.get("is_legacy_scrap_item"):
						item.additional_cost_per_qty = (
							(item.amount * self.total_additional_costs) / total_amt
						) / item.qty
			else:
				total_qty = sum(
					flt(item.qty)
					for item in self.get("items")
					if not item.get("type") and not item.get("is_legacy_scrap_item")
				)
				additional_cost_per_qty = self.total_additional_costs / total_qty
				for item in self.items:
					if not item.get("type") and not item.get("is_legacy_scrap_item"):
						item.additional_cost_per_qty = additional_cost_per_qty
		else:
			for item in self.items:
				if not item.get("type") and not item.get("is_legacy_scrap_item"):
					item.additional_cost_per_qty = 0

	@frappe.whitelist()
	def get_current_stock(self):
		if self.doctype in ["Purchase Receipt", "Subcontracting Receipt"]:
			for item in self.get("supplied_items"):
				if self.supplier_warehouse:
					actual_qty = frappe.db.get_value(
						"Bin",
						{"item_code": item.rm_item_code, "warehouse": self.supplier_warehouse},
						"actual_qty",
					)
					item.current_stock = flt(actual_qty)

	@property
	def sub_contracted_items(self):
		if not hasattr(self, "_sub_contracted_items"):
			self._sub_contracted_items = []
			item_codes = list(set(item.item_code for item in self.get("items")))
			if item_codes:
				items = frappe.get_all(
					"Item", filters={"name": ["in", item_codes], "is_sub_contracted_item": 1}
				)
				self._sub_contracted_items = [item.name for item in items]

		return self._sub_contracted_items

	def update_requested_qty(self):
		material_request_map = {}
		for d in self.get("items"):
			if d.material_request_item:
				material_request_map.setdefault(d.material_request, []).append(d.material_request_item)

		for mr, mr_item_rows in material_request_map.items():
			if mr and mr_item_rows:
				mr_obj = frappe.get_doc("Material Request", mr)

				if mr_obj.status in ["Stopped", "Cancelled"]:
					frappe.throw(
						_("Material Request {0} is cancelled or stopped").format(mr),
						frappe.InvalidStatusError,
					)

				mr_obj.update_requested_qty(mr_item_rows)


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
