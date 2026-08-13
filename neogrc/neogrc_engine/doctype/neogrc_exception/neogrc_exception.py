# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from neogrc import crosswalk


class NeoGRCException(Document):
	def validate(self):
		self.control = crosswalk.resolve_control(self.control_framework, self.control_id)

		if not self.expires_at:
			frappe.throw(
				_("Every exception must be time-bound. Set an expiry date.")
			)

		if getdate(self.expires_at) <= getdate(self.requested_on or nowdate()):
			frappe.throw(_("Expiry must be after the request date"))

		if self.status == "approved":
			if not self.approved_by:
				frappe.throw(_("An approved exception needs an approver"))
			if self.approved_by == self.owner_user:
				frappe.throw(
					_("The exception owner cannot approve their own exception. "
					  "Segregation of duties applies here.")
				)
			if not self.compensating_controls:
				frappe.msgprint(
					_("This exception is approved with no compensating control recorded."),
					indicator="orange",
					alert=True,
				)

		if getdate(self.expires_at) < getdate(nowdate()) and self.status == "approved":
			self.status = "expired"
