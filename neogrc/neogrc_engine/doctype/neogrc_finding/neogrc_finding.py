# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from neogrc import contract, crosswalk


class NeoGRCFinding(Document):
	def validate(self):
		self.validate_contract()
		self.resolve_controls()
		self.set_rollups()

	def validate_contract(self):
		"""Re-run the contract rules on desk edits.

		API ingestion validates the raw payload; this catches a user typing a
		failing evaluation into the grid with no message, which would otherwise
		produce an unauditable record.
		"""
		if not self.evaluations:
			frappe.throw(_("A Finding needs at least one evaluation"))

		for row in self.evaluations:
			if row.status == "fail":
				if not (row.message or "").strip():
					frappe.throw(
						_("Row {0}: a failing evaluation must carry a message").format(row.idx)
					)
				if not row.severity:
					frappe.throw(
						_("Row {0}: a failing evaluation must carry a severity").format(row.idx)
					)
			elif row.status == "inconclusive" and not (row.message or "").strip():
				frappe.throw(
					_("Row {0}: an inconclusive evaluation must explain why").format(row.idx)
				)

		for field in ("resource_tags", "raw_attributes", "metadata"):
			value = self.get(field)
			if value:
				try:
					frappe.parse_json(value)
				except Exception:
					frappe.throw(_("{0} must be valid JSON").format(_(field)))

	def resolve_controls(self):
		for row in self.evaluations:
			if not row.control:
				row.control = crosswalk.resolve_control(row.control_framework, row.control_id)

	def set_rollups(self):
		self.rollup_status = contract.rollup_status(self.evaluations)
		self.worst_severity = contract.worst_severity(self.evaluations)

	def on_update(self):
		if self.disposition in ("Remediated", "Risk Accepted", "False Positive") and not self.resolution_note:
			frappe.msgprint(
				_("Consider recording a resolution note - an auditor will ask why this was closed."),
				indicator="orange",
				alert=True,
			)
