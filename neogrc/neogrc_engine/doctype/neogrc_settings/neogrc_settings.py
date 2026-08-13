# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import os

import frappe
from frappe import _
from frappe.model.document import Document

from neogrc import crosswalk


class NeoGRCSettings(Document):
	def validate(self):
		if self.evidence_base_path:
			if not os.path.isabs(self.evidence_base_path):
				frappe.throw(_("Evidence base path must be absolute"))
			try:
				os.makedirs(self.evidence_base_path, mode=0o750, exist_ok=True)
			except OSError as exc:
				frappe.throw(
					_("Cannot create the evidence directory: {0}").format(exc)
				)

		if self.ai_enabled and self.residency_mode == "On-Premise Only":
			host = (self.ai_endpoint or "")
			if not any(h in host for h in ("127.0.0.1", "localhost", "::1")) and not self._allowed(host):
				frappe.throw(
					_("Residency mode is On-Premise Only, so the AI endpoint must be local "
					  "or explicitly allow-listed.")
				)

		if self.artifact_retention_days and self.artifact_retention_days < 365:
			frappe.msgprint(
				_("Most audit regimes expect at least 12 months of evidence retention."),
				indicator="orange",
				alert=True,
			)

	def _allowed(self, host: str) -> bool:
		allowed = [h.strip() for h in (self.allowed_egress_hosts or "").splitlines() if h.strip()]
		return any(h in host for h in allowed)

	def on_update(self):
		crosswalk.clear_cache()
