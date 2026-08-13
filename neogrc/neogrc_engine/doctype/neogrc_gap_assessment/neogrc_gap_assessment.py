# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now

from neogrc.engine import gap


class NeoGRCGapAssessment(Document):
	def validate(self):
		if not self.as_of:
			self.as_of = now()

		seen = set()
		for row in self.frameworks:
			if row.framework in seen:
				frappe.throw(_("Framework {0} is listed twice").format(row.framework))
			seen.add(row.framework)

		if self.docstatus == 0 and self.status == "Completed" and not self.results:
			self.status = "Draft"

	def before_submit(self):
		if self.status != "Completed":
			frappe.throw(
				_("Run the assessment before submitting it. A submitted assessment is an "
				  "audit record and must contain results.")
			)

	@frappe.whitelist()
	def run(self):
		"""Queue this assessment. Exposed to the form button."""
		self.check_permission("write")
		gap.enqueue_assessment(self.name)
		frappe.msgprint(
			_("Assessment queued. Results will appear here when the job completes."),
			alert=True,
		)
		return self.name

	@frappe.whitelist()
	def optimisation_plan(self):
		self.check_permission("read")
		return gap.optimise_multi_framework(self.name)
