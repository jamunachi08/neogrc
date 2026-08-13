# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import os

import frappe
from frappe import _
from frappe.model.document import Document


class NeoGRCConnector(Document):
	def validate(self):
		self.connector_id = (self.connector_id or "").strip().lower()

		if self.handler_type == "Shell":
			if not self.script_root:
				frappe.throw(_("A shell connector needs a script root"))
			if not os.path.isabs(self.script_root):
				frappe.throw(_("Script root must be an absolute path"))
			self.script_root = os.path.normpath(self.script_root)

		if self.auth_mode in ("Site Config Key", "Frappe Credential") and not self.credential_key:
			frappe.throw(_("Record which key holds the credential"))

	@frappe.whitelist()
	def preflight(self):
		"""Check binaries, environment and script root without running anything."""
		self.check_permission("read")
		from neogrc.engine.evidence_runner import _preflight

		ready, reason, code = _preflight(self)
		return {"ready": ready, "reason": reason, "exit_code": code}
