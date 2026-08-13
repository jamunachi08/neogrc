# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_to_date, now

from neogrc.contract import SEVERITY_RANK


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["f.disposition IN ('Open', 'Acknowledged')"]
	values = {"cutoff": add_to_date(now(), days=-int(filters.get("lookback_days") or 90))}

	if filters.get("framework"):
		conditions.append("e.control_framework = %(framework)s")
		values["framework"] = filters.framework
	if filters.get("source"):
		conditions.append("f.source = %(source)s")
		values["source"] = filters.source
	if filters.get("status"):
		conditions.append("e.status = %(status)s")
		values["status"] = filters.status
	else:
		conditions.append("e.status = 'fail'")

	rows = frappe.db.sql(
		f"""
		SELECT
			e.control_framework, e.control_id, e.control, e.status, e.severity,
			COUNT(DISTINCT f.resource_id) AS resources,
			COUNT(*) AS evaluations,
			MAX(f.collected_at) AS last_seen,
			GROUP_CONCAT(DISTINCT f.source) AS sources
		FROM `tabNeoGRC Finding` f
		INNER JOIN `tabNeoGRC Finding Evaluation` e ON e.parent = f.name
		WHERE f.collected_at >= %(cutoff)s AND {' AND '.join(conditions)}
		GROUP BY e.control_framework, e.control_id, e.control, e.status, e.severity
		""",
		values,
		as_dict=True,
	)

	for row in rows:
		row["control_title"] = (
			frappe.db.get_value("NeoGRC Control", row.control, "control_title") if row.control else ""
		)
		row["weight"] = SEVERITY_RANK.get(row.severity or "", 0)

	rows.sort(key=lambda r: (-r["weight"], -r["resources"]))
	return _columns(), rows


def _columns():
	return [
		{"fieldname": "control_framework", "label": _("Framework"), "fieldtype": "Link",
		 "options": "NeoGRC Framework", "width": 150},
		{"fieldname": "control_id", "label": _("Control"), "fieldtype": "Data", "width": 110},
		{"fieldname": "control_title", "label": _("Title"), "fieldtype": "Data", "width": 250},
		{"fieldname": "severity", "label": _("Severity"), "fieldtype": "Data", "width": 90},
		{"fieldname": "resources", "label": _("Resources"), "fieldtype": "Int", "width": 100},
		{"fieldname": "evaluations", "label": _("Evaluations"), "fieldtype": "Int", "width": 110},
		{"fieldname": "sources", "label": _("Sources"), "fieldtype": "Data", "width": 200},
		{"fieldname": "last_seen", "label": _("Last Seen"), "fieldtype": "Datetime", "width": 160},
	]
