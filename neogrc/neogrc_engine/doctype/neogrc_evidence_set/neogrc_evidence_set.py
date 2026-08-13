# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from neogrc.engine import evidence_runner


class NeoGRCEvidenceSet(Document):
	def validate(self):
		seen = set()
		for row in self.fetchers:
			if row.fetcher in seen:
				frappe.throw(_("Fetcher {0} is listed twice").format(row.fetcher))
			seen.add(row.fetcher)

		if self.schedule != "Manual" and not self.next_run_on:
			from frappe.utils import nowdate

			self.next_run_on = nowdate()

	@frappe.whitelist()
	def run_now(self):
		self.check_permission("write")
		run = evidence_runner.enqueue_evidence_set(self.name, "Manual")
		frappe.msgprint(_("Evidence run {0} queued.").format(run), alert=True)
		return run
