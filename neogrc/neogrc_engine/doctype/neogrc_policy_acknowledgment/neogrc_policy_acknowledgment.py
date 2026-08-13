# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now


class NeoGRCPolicyAcknowledgment(Document):
	def validate(self):
		if not self.employee and not self.user:
			frappe.throw(_("An acknowledgment needs either an Employee or a User"))

		self.enforce_own_signature()

		if self.acknowledged and not self.acknowledged_on:
			self.acknowledged_on = now()
		if not self.acknowledged:
			self.acknowledged_on = None

	def enforce_own_signature(self):
		"""Only the subject may mark their own row acknowledged.

		Without this, a manager could sign off the whole workforce in a bulk edit
		and the awareness evidence would be worth nothing. Managers can still
		create, reassign and delete rows - they just cannot sign for someone else.
		"""
		if not self.has_value_changed("acknowledged") or not self.acknowledged:
			return
		if frappe.session.user in ("Administrator",):
			return

		subject = self.user
		if not subject and self.employee:
			subject = frappe.db.get_value("Employee", self.employee, "user_id")

		if subject and subject != frappe.session.user:
			frappe.throw(
				_("Only {0} can acknowledge this policy. Acknowledgment recorded by "
				  "someone else is not evidence of awareness.").format(subject),
				frappe.PermissionError,
			)


def create_for_policy(policy_name: str) -> int:
	"""Create one acknowledgment row per active person for a published policy.

	Idempotent on (policy, policy_version, subject), so re-approving a policy at
	the same version does not duplicate rows, while bumping the version issues a
	fresh round of sign-off.
	"""
	policy = frappe.get_doc("NeoGRC Policy", policy_name)
	version = policy.version or "1.0"

	existing = set(
		frappe.get_all(
			"NeoGRC Policy Acknowledgment",
			filters={"policy": policy_name, "policy_version": version},
			pluck="employee",
		)
	) | set(
		frappe.get_all(
			"NeoGRC Policy Acknowledgment",
			filters={"policy": policy_name, "policy_version": version},
			pluck="user",
		)
	)

	subjects = []
	if frappe.db.exists("DocType", "Employee"):
		subjects = [
			{"employee": name}
			for name in frappe.get_all("Employee", filters={"status": "Active"}, pluck="name")
			if name not in existing
		]

	if not subjects:
		# Sites without ERPNext still need awareness evidence; fall back to
		# enabled desk users rather than producing nothing.
		subjects = [
			{"user": name}
			for name in frappe.get_all(
				"User", filters={"enabled": 1, "user_type": "System User"}, pluck="name"
			)
			if name not in existing and name not in ("Administrator", "Guest")
		]

	created = 0
	for subject in subjects:
		doc = frappe.new_doc("NeoGRC Policy Acknowledgment")
		doc.policy = policy_name
		doc.policy_version = version
		doc.due_on = policy.effective_at
		doc.update(subject)
		doc.flags.ignore_permissions = True
		doc.insert()
		created += 1

	return created


@frappe.whitelist()
def issue_acknowledgments(policy: str) -> dict:
	"""Whitelisted trigger from the Policy form."""
	frappe.has_permission("NeoGRC Policy", "write", throw=True)
	created = frappe.enqueue(
		"neogrc.neogrc_engine.doctype.neogrc_policy_acknowledgment"
		".neogrc_policy_acknowledgment.create_for_policy",
		queue="long",
		policy_name=policy,
	)
	return {"queued": bool(created), "policy": policy}
