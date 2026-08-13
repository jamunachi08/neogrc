# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, getdate, nowdate


class NeoGRCPolicy(Document):
	def validate(self):
		if self.status == "approved":
			if not self.approvers:
				frappe.throw(_("An approved policy needs at least one recorded approver"))
			if not any(row.approved_on for row in self.approvers):
				frappe.throw(_("Record the approval date against at least one approver"))
			if not self.effective_at:
				self.effective_at = nowdate()
			if not (self.document_file or self.document_path):
				frappe.throw(
					_("An approved policy must point at a document, either attached or by path")
				)

		if self.effective_at and not self.next_review_at:
			self.next_review_at = add_days(
				getdate(self.effective_at), cint(self.review_interval_days) or 365
			)

	def on_update(self):
		# Approving a policy is what triggers the awareness round. Doing it here
		# rather than on a workflow state keeps it working on sites that have not
		# installed a workflow.
		if self.has_value_changed("status") and self.status == "approved":
			self.issue_acknowledgments()

		if (
			self.status == "approved"
			and self.next_review_at
			and getdate(self.next_review_at) < getdate(nowdate())
		):
			frappe.msgprint(
				_("This policy is past its review date."), indicator="red", alert=True
			)

	def issue_acknowledgments(self):
		"""Queue one acknowledgment row per active person.

		Queued rather than inline: a site with several thousand employees would
		otherwise hold a web worker for the length of the insert loop.
		"""
		frappe.enqueue(
			"neogrc.neogrc_engine.doctype.neogrc_policy_acknowledgment"
			".neogrc_policy_acknowledgment.create_for_policy",
			queue="long",
			policy_name=self.name,
			enqueue_after_commit=True,
		)
		frappe.msgprint(
			_("Acknowledgment records are being issued for policy version {0}.").format(
				self.version or "1.0"
			),
			alert=True,
		)
