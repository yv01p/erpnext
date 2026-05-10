import frappe
from frappe.query_builder import DocType


def execute():
	apps = frappe.get_installed_apps()

	if "erpnext" not in apps or "payments" not in apps:
		return

	doc = frappe.get_doc("DocType", "Payment Gateway Account")
	doc.save()

	from payments.utils import make_payments_erpnext_custom_fields

	make_payments_erpnext_custom_fields()

	PG = DocType("Payment Gateway")

	update_pg_query = frappe.qb.update(PG).set(PG.gateway_name, PG.gateway)
	update_pg_query.run()

	PGA = DocType("Payment Gateway Account")

	update_pga_query = (
		frappe.qb.update(PGA)
		.set(PGA.parent, PGA.payment_gateway)
		.set(PGA.parenttype, "Payment Gateway")
		.set(PGA.parentfield, "payment_gateway_account")
	)
	update_pga_query.run()

	sp_list = frappe.get_all(
		"Subscription Plan",
		fields=["name", "payment_gateway"],
	)

	for sp in sp_list:
		if sp.payment_gateway:
			doc_dict = frappe.db.get_value(
				"Payment Gateway Account",
				sp.payment_gateway,
				["parent", "payment_account"],
				as_dict=True,
			)

			if not doc_dict:
				continue

			frappe.db.set_value(
				"Subscription Plan",
				sp.name,
				{
					"payment_gateway": doc_dict.parent,
					"payment_account": doc_dict.payment_account,
				},
			)

	pr_list = frappe.get_all(
		"Payment Request",
		fields=["name", "payment_gateway_account"],
	)

	for pr in pr_list:
		if pr.payment_gateway_account:
			doc_dict = frappe.db.get_value(
				"Payment Gateway Account",
				pr.payment_gateway_account,
				["parent", "payment_account", "payment_channel"],
				as_dict=True,
			)

			if not doc_dict:
				continue

			frappe.db.set_value(
				"Payment Request",
				pr.name,
				{
					"payment_gateway": doc_dict.parent,
					"payment_account": doc_dict.payment_account,
					"payment_channel": doc_dict.payment_channel,
				},
			)
