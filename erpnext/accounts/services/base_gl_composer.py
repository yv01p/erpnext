# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

"""Base class for per-document GL entry composers.

A composer assembles the list of GL entry dicts for a single voucher. Unlike
the posting sink (``general_ledger.make_gl_entries``) and the stateless
validators (``gl_validator``), composing is stateful and per-document, so it is
modelled as a class holding the document being composed. Subclasses implement
``compose`` to return the voucher-specific list of GL entries.
"""


class BaseGLComposer:
	def __init__(self, doc):
		self.doc = doc

	def compose(self):
		raise NotImplementedError
