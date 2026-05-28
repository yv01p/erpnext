# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, get_link_to_form

from erpnext.controllers.subcontracting import data_assembly
from erpnext.stock.doctype.batch.batch import get_batch_qty
from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
	combine_datetime,
	get_auto_batch_nos,
	get_available_serial_nos,
)
from erpnext.stock.serial_batch_bundle import SerialBatchCreation, get_serial_nos_from_bundle
from erpnext.stock.utils import get_incoming_rate


class SuppliedItemsHelper:
	def __init__(self, controller):
		self.controller = controller

	def set_valuation_rate_for_rm(self):
		rate_changed = False
		if self.controller.doctype == "Subcontracting Receipt":
			for row in self.controller.supplied_items:
				kwargs = frappe._dict(
					{
						"item_code": row.rm_item_code,
						"warehouse": self.controller.supplier_warehouse,
						"posting_date": self.controller.posting_date,
						"posting_time": self.controller.posting_time,
						"qty": flt(row.consumed_qty) * (-1 if not self.controller.is_return else 1),
						"voucher_type": self.controller.doctype,
						"voucher_no": self.controller.name,
						"company": self.controller.company,
						"serial_and_batch_bundle": row.serial_and_batch_bundle,
						"voucher_detail_no": row.name,
						"batch_no": row.batch_no,
						"serial_no": row.serial_no,
						"use_serial_batch_fields": row.use_serial_batch_fields,
					}
				)

				rate = get_incoming_rate(kwargs)
				precision = frappe.get_precision("Subcontracting Receipt Supplied Item", "rate")
				if flt(rate, precision) != flt(row.rate, precision):
					row.rate = rate
					row.amount = flt(row.consumed_qty) * flt(rate)
					rate_changed = True

		if rate_changed:
			self.controller.calculate_items_qty_and_amount()

	def _remove_changed_rows(self):
		if not self.controller._changed_name:
			return

		i = 1
		self.controller.set(self.controller.raw_material_table, [])
		for item in self.controller._doc_before_save.supplied_items:
			if item.reference_name in self.controller._changed_name:
				self._remove_serial_and_batch_bundle(item)
				continue

			if item.reference_name not in self.controller._reference_name:
				continue

			item.idx = i
			self.controller.append("supplied_items", item)

			i += 1

	def _remove_serial_and_batch_bundle(self, item):
		if item.get("serial_and_batch_bundle"):
			frappe.delete_doc("Serial and Batch Bundle", item.serial_and_batch_bundle, force=True)

	def _get_materials_from_bom(self, item_code, bom_no, exploded_item=0):
		data = []

		doctype = "BOM Item" if not exploded_item else "BOM Explosion Item"
		fields = [
			{"DIV": [f"`tab{doctype}`.`stock_qty`", "`tabBOM`.`quantity`"], "as": "qty_consumed_per_unit"}
		]

		alias_dict = {
			"item_code": "rm_item_code",
			"name": "bom_detail_no",
			"source_warehouse": "reserve_warehouse",
		}
		fields_list = [
			"item_code",
			"name",
			"rate",
			"stock_uom",
			"source_warehouse",
			"description",
			"item_name",
			"stock_uom",
		]

		if doctype == "BOM Item":
			fields_list.extend(["is_phantom_item", "bom_no"])

		for field in fields_list:
			fields.append(f"`tab{doctype}`.`{field}` As {alias_dict.get(field, field)}")

		filters = [
			[doctype, "parent", "=", bom_no],
			[doctype, "docstatus", "=", 1],
			["BOM", "item", "=", item_code],
			[doctype, "sourced_by_supplier", "=", 0],
		]

		data = frappe.get_all("BOM", fields=fields, filters=filters, order_by=f"`tab{doctype}`.`idx`") or []
		to_remove = []
		for item in data:
			if item.is_phantom_item:
				data += self._get_materials_from_bom(
					item.rm_item_code, item.bom_no, exploded_item=exploded_item
				)
				to_remove.append(item)

		for item in to_remove:
			data.remove(item)

		return data

	def _update_reserve_warehouse(self, row, item):
		if (
			self.controller.doctype == self.controller.subcontract_data.order_doctype
			and self.controller.doctype != "Subcontracting Inward Order"
		):
			row.reserve_warehouse = self.controller.set_reserve_warehouse or item.warehouse
		elif frappe.get_cached_value("Item", row.rm_item_code, "is_customer_provided_item") and self.controller.get(
			"customer_warehouse"
		):
			row.warehouse = self.controller.customer_warehouse

	def _set_alternative_item(self, bom_item):
		if self.controller.alternative_item_details.get(bom_item.rm_item_code):
			bom_item.update(self.controller.alternative_item_details[bom_item.rm_item_code])

	def _set_serial_and_batch_bundle(self, item_row, rm_obj, qty):
		key = (rm_obj.rm_item_code, item_row.item_code, item_row.get(self.controller.subcontract_data.order_field))
		if not self.controller.available_materials.get(key):
			return

		if not self.controller.available_materials[key]["serial_no"] and not self.controller.available_materials[key]["batch_no"]:
			return

		serial_nos = []
		batches = frappe._dict({})

		if self.controller.available_materials.get(key) and self.controller.available_materials[key]["serial_no"]:
			serial_nos = self._get_serial_nos_for_bundle(qty, key)

		elif self.controller.available_materials.get(key) and self.controller.available_materials[key]["batch_no"]:
			batches = self._get_batch_nos_for_bundle(qty, key)

		bundle = SerialBatchCreation(
			frappe._dict(
				{
					"company": self.controller.company,
					"item_code": rm_obj.rm_item_code,
					"warehouse": self.controller.supplier_warehouse,
					"qty": qty,
					"serial_nos": serial_nos,
					"batches": batches,
					"posting_datetime": combine_datetime(self.controller.posting_date, self.controller.posting_time),
					"voucher_type": "Subcontracting Receipt",
					"do_not_submit": True,
					"type_of_transaction": "Outward" if qty > 0 else "Inward",
				}
			)
		).make_serial_and_batch_bundle()

		return bundle.name

	def _get_batch_nos_for_bundle(self, qty, key):
		available_batches = defaultdict(float)

		precision = frappe.get_precision("Subcontracting Receipt Supplied Item", "consumed_qty")
		for batch_no, batch_qty in self.controller.available_materials[key]["batch_no"].items():
			if flt(batch_qty, precision) <= 0:
				continue

			qty_to_consumed = 0
			if qty > 0:
				if batch_qty >= qty:
					qty_to_consumed = qty
				else:
					qty_to_consumed = batch_qty

				qty -= qty_to_consumed
				if qty_to_consumed > 0:
					available_batches[batch_no] += qty_to_consumed
					self.controller.available_materials[key]["batch_no"][batch_no] -= qty_to_consumed

		return available_batches

	def _get_serial_nos_for_bundle(self, qty, key):
		available_sns = sorted(self.controller.available_materials[key]["serial_no"])[0 : cint(qty)]
		serial_nos = []

		for serial_no in available_sns:
			serial_nos.append(serial_no)

			self.controller.available_materials[key]["serial_no"].remove(serial_no)

		return serial_nos

	def _add_supplied_or_received_item(self, item_row, bom_item, qty):
		bom_item.conversion_factor = item_row.conversion_factor
		if self.controller.subcontract_data.order_doctype == "Subcontracting Inward Order":
			bom_item.pop("rate")
		rm_obj = self.controller.append(self.controller.raw_material_table, bom_item)
		if rm_obj.get("qty"):
			# Qty field not exists
			rm_obj.qty = 0.0

		rm_obj.reference_name = item_row.name

		use_serial_batch_fields = frappe.get_single_value("Stock Settings", "use_serial_batch_fields")

		if self.controller.doctype == self.controller.subcontract_data.order_doctype:
			rm_obj.required_qty = flt(qty, rm_obj.precision("required_qty"))
			if self.controller.doctype != "Subcontracting Inward Order":
				rm_obj.amount = flt(rm_obj.required_qty * rm_obj.rate, rm_obj.precision("amount"))
		else:
			rm_obj.consumed_qty = flt(qty, rm_obj.precision("consumed_qty"))
			rm_obj.required_qty = flt(bom_item.required_qty or qty, rm_obj.precision("required_qty"))
			rm_obj.serial_and_batch_bundle = None
			setattr(
				rm_obj, self.controller.subcontract_data.order_field, item_row.get(self.controller.subcontract_data.order_field)
			)

			if use_serial_batch_fields:
				rm_obj.use_serial_batch_fields = 1
				if not self.controller.flags.get("reset_raw_materials"):
					self._set_batch_nos(bom_item, item_row, rm_obj, qty)

		if self.controller.doctype == "Subcontracting Receipt":
			if not use_serial_batch_fields:
				rm_obj.serial_and_batch_bundle = self._set_serial_and_batch_bundle(
					item_row, rm_obj, rm_obj.consumed_qty
				)

				self.set_rate_for_supplied_items(rm_obj, item_row)
			elif self.controller.backflush_based_on == "BOM":
				self.update_rate_for_supplied_items()
				self.set_batch_for_supplied_items()

	def set_batch_for_supplied_items(self):
		from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos_for_outward
		from erpnext.stock.get_item_details import get_filtered_serial_nos

		if self.controller.is_return:
			return

		for row in self.controller.supplied_items:
			item_details = frappe.get_cached_value(
				"Item", row.rm_item_code, ["has_batch_no", "has_serial_no"], as_dict=1
			)

			if not item_details.has_batch_no and not item_details.has_serial_no:
				continue

			if not row.use_serial_batch_fields:
				continue

			kwargs = frappe._dict(
				{
					"item_code": row.rm_item_code,
					"warehouse": self.controller.supplier_warehouse,
					"posting_date": self.controller.posting_date,
					"posting_time": self.controller.posting_time,
					"qty": flt(row.consumed_qty),
				}
			)

			if item_details.has_serial_no and not row.serial_and_batch_bundle and not row.serial_no:
				serial_nos = get_available_serial_nos(kwargs)
				if serial_nos:
					serial_nos = [sn.get("serial_no") for sn in serial_nos]
					serial_nos = get_filtered_serial_nos(serial_nos, self.controller, "supplied_items")
					row.serial_no = "\n".join(serial_nos)

			elif (
				item_details.has_batch_no
				and not row.serial_and_batch_bundle
				and (not row.batch_no or self.batch_has_not_available(row.batch_no, row.consumed_qty))
			):
				batches = get_auto_batch_nos(kwargs)
				if batches:
					consumed_qty = row.consumed_qty
					for index, d in enumerate(batches):
						if consumed_qty <= 0:
							break

						if index == 0:
							row.batch_no = d.get("batch_no")
							row.consumed_qty = d.get("qty")
							consumed_qty -= d.get("qty")
						else:
							new_row = self.controller.append("supplied_items", {})
							new_row.update(frappe.copy_doc(row).as_dict())
							new_row.update(
								{
									"consumed_qty": d.get("qty"),
									"batch_no": d.get("batch_no"),
									"rate": row.rate,
									"amount": flt(d.get("qty")) * flt(row.rate),
								}
							)
							consumed_qty -= d.get("qty")

	def batch_has_not_available(self, batch_no, qty_required):
		batch_qty = get_batch_qty(batch_no, self.controller.supplier_warehouse, consider_negative_batches=True)

		return batch_qty < qty_required

	def update_rate_for_supplied_items(self):
		if self.controller.doctype != "Subcontracting Receipt":
			return

		for row in self.controller.supplied_items:
			item_row = None
			if row.reference_name:
				item_row = self.get_item_row(row.reference_name)

			if not item_row:
				continue

			self.set_rate_for_supplied_items(row, item_row)

	def get_item_row(self, reference_name):
		for item in self.controller.items:
			if item.name == reference_name:
				return item

	def set_rate_for_supplied_items(self, rm_obj, item_row):
		args = frappe._dict(
			{
				"item_code": rm_obj.rm_item_code,
				"warehouse": self.controller.supplier_warehouse,
				"posting_date": self.controller.posting_date,
				"posting_time": self.controller.posting_time,
				"qty": -1 * flt(rm_obj.consumed_qty),
				"actual_qty": -1 * flt(rm_obj.consumed_qty),
				"voucher_type": self.controller.doctype,
				"voucher_no": self.controller.name,
				"voucher_detail_no": item_row.name,
				"company": self.controller.company,
				"allow_zero_valuation": 1,
			}
		)

		if rm_obj.serial_and_batch_bundle:
			args["serial_and_batch_bundle"] = rm_obj.serial_and_batch_bundle

		if rm_obj.use_serial_batch_fields:
			args["batch_no"] = rm_obj.batch_no
			args["serial_no"] = rm_obj.serial_no

		rm_obj.rate = get_incoming_rate(args)

	def _set_batch_nos(self, bom_item, item_row, rm_obj, qty):
		key = (rm_obj.rm_item_code, item_row.item_code, item_row.get(self.controller.subcontract_data.order_field))

		if self.controller.available_materials.get(key) and self.controller.available_materials[key]["batch_no"]:
			new_rm_obj = None
			for batch_no, batch_qty in self.controller.available_materials[key]["batch_no"].items():
				if batch_qty >= qty or (
					rm_obj.consumed_qty == 0
					and self.controller.backflush_based_on == "BOM"
					and len(self.controller.available_materials[key]["batch_no"]) == 1
				):
					if rm_obj.consumed_qty == 0:
						self._set_consumed_qty(rm_obj, qty)

					self._set_batch_no_as_per_qty(item_row, rm_obj, batch_no, qty)
					self.controller.available_materials[key]["batch_no"][batch_no] -= qty
					return

				elif qty > 0 and batch_qty > 0:
					qty -= batch_qty
					new_rm_obj = self.controller.append(self.controller.raw_material_table, bom_item)
					new_rm_obj.serial_and_batch_bundle = None
					new_rm_obj.use_serial_batch_fields = 1
					new_rm_obj.reference_name = item_row.name
					self._set_batch_no_as_per_qty(item_row, new_rm_obj, batch_no, batch_qty)
					self.controller.available_materials[key]["batch_no"][batch_no] = 0

			if new_rm_obj:
				self.controller.remove(rm_obj)
			elif abs(qty) > 0:
				self._set_consumed_qty(rm_obj, qty)

		else:
			self._set_consumed_qty(rm_obj, qty, bom_item.required_qty or qty)
			self._set_serial_nos(item_row, rm_obj)

	def _set_consumed_qty(self, rm_obj, consumed_qty, required_qty=0):
		rm_obj.required_qty = flt(required_qty, rm_obj.precision("required_qty"))
		rm_obj.consumed_qty = flt(consumed_qty, rm_obj.precision("consumed_qty"))

	def _set_serial_nos(self, item_row, rm_obj):
		key = (rm_obj.rm_item_code, item_row.item_code, item_row.get(self.controller.subcontract_data.order_field))
		if self.controller.available_materials.get(key) and self.controller.available_materials[key]["serial_no"]:
			used_serial_nos = self.controller.available_materials[key]["serial_no"][0 : cint(rm_obj.consumed_qty)]
			rm_obj.serial_no = "\n".join(used_serial_nos)

			# Removed the used serial nos from the list
			for sn in used_serial_nos:
				self.controller.available_materials[key]["serial_no"].remove(sn)

	def _set_batch_no_as_per_qty(self, item_row, rm_obj, batch_no, qty):
		rm_obj.update(
			{
				"consumed_qty": qty,
				"batch_no": batch_no,
				"required_qty": qty,
				self.controller.subcontract_data.order_field: item_row.get(self.controller.subcontract_data.order_field),
			}
		)

		self._set_serial_nos(item_row, rm_obj)

	def _get_qty_based_on_material_transfer(self, item_row, transfer_item):
		key = (
			item_row.item_code,
			item_row.get(self.controller.subcontract_data.order_field),
			item_row.get("bom"),
		)

		if self.controller.qty_to_be_received == item_row.qty:
			return transfer_item.qty

		if self.controller.qty_to_be_received.get(key):
			qty = (flt(item_row.qty) * flt(transfer_item.qty)) / flt(self.controller.qty_to_be_received.get(key))
			transfer_item.item_details.required_qty = transfer_item.qty

			if transfer_item.serial_no or frappe.get_cached_value(
				"UOM", transfer_item.item_details.stock_uom, "must_be_whole_number"
			):
				return frappe.utils.ceil(qty)

			return qty

	def _set_supplied_or_received_items(self):
		self.controller.bom_items = {}

		has_items = True if self.controller.get(self.controller.raw_material_table) else False
		for row in self.controller.items:
			if self.controller.doctype != self.controller.subcontract_data.order_doctype and (
				(self.controller._changed_name and row.name not in self.controller._changed_name)
				or (has_items and not self.controller._changed_name)
			):
				continue

			if self.controller.doctype == self.controller.subcontract_data.order_doctype or (
				self.controller.backflush_based_on == "BOM" or self.controller.is_return
			):
				for bom_item in self._get_materials_from_bom(
					row.item_code, row.bom, row.get("include_exploded_items")
				):
					qty = (
						flt(bom_item.qty_consumed_per_unit)
						* flt(row.get("received_qty") or (row.qty + (row.get("rejected_qty") or 0)))
						* row.conversion_factor
					)
					bom_item.main_item_code = row.item_code
					self._update_reserve_warehouse(bom_item, row)
					self._set_alternative_item(bom_item)
					self._add_supplied_or_received_item(row, bom_item, qty)

			elif self.controller.backflush_based_on != "BOM":
				for key, transfer_item in self.controller.available_materials.items():
					if (key[1], key[2]) == (
						row.item_code,
						row.get(self.controller.subcontract_data.order_field),
					) and transfer_item.qty > 0:
						qty = flt(self._get_qty_based_on_material_transfer(row, transfer_item))
						transfer_item.qty -= qty
						self._add_supplied_or_received_item(row, transfer_item.get("item_details"), qty)

				if self.controller.qty_to_be_received:
					self.controller.qty_to_be_received[
						(
							row.item_code,
							row.get(self.controller.subcontract_data.order_field),
							row.get("bom"),
						)
					] -= row.qty

	def _set_rate_for_serial_and_batch_bundle(self):
		if self.controller.doctype != "Subcontracting Receipt":
			return

		for row in self.controller.get(self.controller.raw_material_table):
			if not row.get("serial_and_batch_bundle"):
				continue

			row.rate = frappe.get_cached_value(
				"Serial and Batch Bundle", row.serial_and_batch_bundle, "avg_rate"
			)

	def _modify_serial_and_batch_bundle(self):
		if self.controller.is_new():
			return

		if self.controller.doctype != "Subcontracting Receipt":
			return

		for item_row in self.controller.items:
			if self.controller._changed_name and item_row.name in self.controller._changed_name:
				continue

			modified_data = self._get_bundle_to_modify(item_row.name)
			if modified_data:
				serial_nos = []
				batches = frappe._dict({})
				key = (
					modified_data.rm_item_code,
					item_row.item_code,
					item_row.get(self.controller.subcontract_data.order_field),
				)

				if self.controller.available_materials.get(key) and self.controller.available_materials[key]["serial_no"]:
					serial_nos = self._get_serial_nos_for_bundle(modified_data.consumed_qty, key)

				elif self.controller.available_materials.get(key) and self.controller.available_materials[key]["batch_no"]:
					batches = self._get_batch_nos_for_bundle(modified_data.consumed_qty, key)

				SerialBatchCreation(
					{
						"item_code": modified_data.rm_item_code,
						"warehouse": self.controller.supplier_warehouse,
						"serial_and_batch_bundle": modified_data.serial_and_batch_bundle,
						"type_of_transaction": "Outward",
						"serial_nos": serial_nos,
						"batches": batches,
						"qty": modified_data.consumed_qty * -1,
					}
				).update_serial_and_batch_entries()

	def _get_bundle_to_modify(self, name):
		for row in self.controller.get("supplied_items"):
			if row.reference_name == name and row.serial_and_batch_bundle:
				if row.consumed_qty != abs(
					frappe.get_cached_value(
						"Serial and Batch Bundle", row.serial_and_batch_bundle, "total_qty"
					)
				):
					return row

	def _prepare_supplied_or_received_items(self):
		self.controller.initialized_fields()
		data_assembly._get_subcontract_orders(self.controller)
		data_assembly._get_pending_qty_to_receive(self.controller)
		self.controller.get_available_materials()
		self._remove_changed_rows()
		self._set_supplied_or_received_items()
		self._modify_serial_and_batch_bundle()
		self._set_rate_for_serial_and_batch_bundle()

	def _validate_batch_no(self, row, key):
		if row.get("batch_no") and row.get("batch_no") not in self.controller._transferred_items.get(key).get(
			"batch_no"
		):
			link = get_link_to_form(
				self.controller.subcontract_data.order_doctype, row.get(self.controller.subcontract_data.order_field)
			)
			msg = f'The Batch No {frappe.bold(row.get("batch_no"))} has not supplied against the {self.controller.subcontract_data.order_doctype} {link}'
			frappe.throw(_(msg), title=_("Incorrect Batch Consumed"))

	def _validate_serial_no(self, row, key):
		if row.get("serial_and_batch_bundle") and self.controller._transferred_items.get(key).get("serial_no"):
			serial_nos = get_serial_nos_from_bundle(row.get("serial_and_batch_bundle"))
			incorrect_sn = set(serial_nos).difference(self.controller._transferred_items.get(key).get("serial_no"))

			if incorrect_sn:
				incorrect_sn = "\n".join(incorrect_sn)
				link = get_link_to_form(
					self.controller.subcontract_data.order_doctype, row.get(self.controller.subcontract_data.order_field)
				)
				msg = f"The Serial Nos {incorrect_sn} has not supplied against the {self.controller.subcontract_data.order_doctype} {link}"
				frappe.throw(_(msg), title=_("Incorrect Serial Number Consumed"))

	def _validate_supplied_or_received_items(self):
		if self.controller.doctype not in ["Purchase Invoice", "Purchase Receipt", "Subcontracting Receipt"]:
			return

		if (
			frappe.db.get_single_value("Buying Settings", "backflush_raw_materials_of_subcontract_based_on")
			== "BOM"
		):
			return

		for row in self.controller.get(self.controller.raw_material_table):
			key = (row.rm_item_code, row.main_item_code, row.get(self.controller.subcontract_data.order_field))
			if not self.controller._transferred_items or not self.controller._transferred_items.get(key):
				return

			self._validate_batch_no(row, key)
			self._validate_serial_no(row, key)
