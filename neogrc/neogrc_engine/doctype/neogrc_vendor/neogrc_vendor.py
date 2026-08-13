# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, getdate, nowdate

TIER_REVIEW_DAYS = {"critical": 180, "high": 365, "medium": 365, "low": 730}


class NeoGRCVendor(Document):
	def validate(self):
		if self.processes_personal_data and not (self.dpa_reference or "").strip():
			frappe.throw(
				_("A vendor processing personal data needs a data-processing agreement "
				  "reference. This is a PDPL obligation, not a formality.")
			)

		if self.tier in ("critical", "high") and self.status == "active":
			if not (self.assurance_evidence or "").strip():
				frappe.msgprint(
					_("No assurance evidence recorded for a {0}-tier vendor.").format(self.tier),
					indicator="orange",
					alert=True,
				)
			if not self.owner_user:
				frappe.throw(_("A {0}-tier vendor needs a named internal owner").format(self.tier))

		if not self.review_interval_days:
			self.review_interval_days = TIER_REVIEW_DAYS.get(self.tier, 365)

		if self.last_review_at and not self.next_review_at:
			self.next_review_at = add_days(
				getdate(self.last_review_at), cint(self.review_interval_days)
			)
