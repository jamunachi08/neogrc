# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from neogrc import crosswalk


class NeoGRCFramework(Document):
	def validate(self):
		if self.is_canonical:
			others = frappe.get_all(
				"NeoGRC Framework",
				filters={"is_canonical": 1, "name": ("!=", self.name)},
				pluck="name",
			)
			if others:
				frappe.throw(
					_("{0} is already the canonical framework. Only one framework can "
					  "be canonical, because every crosswalk pivots through it.").format(others[0])
				)

	def on_update(self):
		crosswalk.clear_cache()
