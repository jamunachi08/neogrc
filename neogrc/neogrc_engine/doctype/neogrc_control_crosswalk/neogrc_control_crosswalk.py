# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from neogrc import crosswalk


class NeoGRCControlCrosswalk(Document):
	def validate(self):
		if self.source_framework == self.target_framework:
			frappe.throw(_("A crosswalk must connect two different frameworks"))

		self.target_control = crosswalk.resolve_control(
			self.target_framework, self.target_control_id
		)

		duplicate = frappe.db.exists(
			"NeoGRC Control Crosswalk",
			{
				"source_control": self.source_control,
				"target_framework": self.target_framework,
				"target_control_id": self.target_control_id,
				"name": ("!=", self.name),
			},
		)
		if duplicate:
			frappe.throw(
				_("This mapping already exists as {0}").format(duplicate),
				frappe.DuplicateEntryError,
			)

	def on_update(self):
		crosswalk.clear_cache()

	def on_trash(self):
		crosswalk.clear_cache()
