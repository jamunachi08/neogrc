# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from neogrc.engine.gap import optimise_multi_framework


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("assessment"):
		return _columns(), []

	rows = optimise_multi_framework(filters.assessment)
	for row in rows:
		row["frameworks"] = ", ".join(row["frameworks"])
	return _columns(), rows


def _columns():
	return [
		{"fieldname": "canonical_control", "label": _("Canonical Control"), "fieldtype": "Data",
		 "width": 140},
		{"fieldname": "control_title", "label": _("Title"), "fieldtype": "Data", "width": 260},
		{"fieldname": "controls_resolved", "label": _("Gaps Closed"), "fieldtype": "Int",
		 "width": 110},
		{"fieldname": "framework_count", "label": _("Frameworks"), "fieldtype": "Int", "width": 100},
		{"fieldname": "weighted_risk", "label": _("Weighted Risk"), "fieldtype": "Int", "width": 120},
		{"fieldname": "effort_hours", "label": _("Effort (h)"), "fieldtype": "Float", "width": 100},
		{"fieldname": "roi", "label": _("Risk per Hour"), "fieldtype": "Float", "width": 120},
		{"fieldname": "frameworks", "label": _("Satisfies"), "fieldtype": "Data", "width": 300},
	]
