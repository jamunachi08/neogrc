# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, nowdate

BANDS = ((20, "Critical"), (12, "High"), (6, "Medium"), (0, "Low"))


def band(score: int) -> str:
	for threshold, label in BANDS:
		if score >= threshold:
			return label
	return "Low"


class NeoGRCRisk(Document):
	def validate(self):
		self.validate_scales()
		self.score()
		self.validate_treatment()

	def validate_scales(self):
		for field in (
			"inherent_likelihood", "inherent_impact",
			"residual_likelihood", "residual_impact",
		):
			value = cint(self.get(field))
			if value and not 1 <= value <= 5:
				frappe.throw(_("{0} must be between 1 and 5").format(_(self.meta.get_label(field))))

	def score(self):
		self.inherent_score = cint(self.inherent_likelihood) * cint(self.inherent_impact)
		self.inherent_band = band(self.inherent_score)

		if self.residual_likelihood and self.residual_impact:
			self.residual_score = cint(self.residual_likelihood) * cint(self.residual_impact)
			self.residual_band = band(self.residual_score)
			if self.residual_score > self.inherent_score:
				frappe.msgprint(
					_("Residual risk scores higher than inherent risk. Check the treatment is not making things worse."),
					indicator="red",
					alert=True,
				)
		else:
			self.residual_score = 0
			self.residual_band = None

	def validate_treatment(self):
		if self.treatment == "mitigate" and self.status in ("open", "mitigating"):
			if not (self.treatment_plan or "").strip():
				frappe.throw(_("A risk being mitigated needs a treatment plan"))

		if self.treatment == "accept" and self.residual_band == "Critical":
			frappe.throw(
				_("A critical residual risk cannot be accepted without an approved exception. "
				  "Raise a NeoGRC Exception and link it to this risk.")
			)

		if not self.next_review_at:
			interval = {"Critical": 30, "High": 90, "Medium": 180, "Low": 365}
			self.next_review_at = add_days(
				self.reviewed_at or nowdate(),
				interval.get(self.residual_band or self.inherent_band, 180),
			)
