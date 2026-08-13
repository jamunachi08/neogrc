# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _

from neogrc import crosswalk


def execute(filters=None):
	filters = frappe._dict(filters or {})
	framework = filters.get("framework") or crosswalk.canonical_framework()

	controls = frappe.get_all(
		"NeoGRC Control",
		filters={"framework": framework, "is_active": 1},
		fields=["name", "control_id", "control_title", "family", "automation", "default_severity"],
		order_by="control_id asc",
		limit_page_length=0,
	)

	links = frappe.get_all(
		"NeoGRC Fetcher Control Link",
		fields=["control_framework", "control_id", "parent"],
		limit_page_length=0,
	)

	enabled_fetchers = set(
		frappe.get_all("NeoGRC Evidence Fetcher", filters={"enabled": 1}, pluck="name")
	)

	coverage = {}
	for link in links:
		mapped = crosswalk.expand(link.control_framework, link.control_id, [framework])
		for cid, _via in mapped.get(framework, []):
			coverage.setdefault(cid, set()).add(link.parent)

	rows = []
	for control in controls:
		fetchers = sorted(coverage.get(control.control_id, []))
		live = [f for f in fetchers if f in enabled_fetchers]
		rows.append({
			"control": control.name,
			"control_id": control.control_id,
			"control_title": control.control_title,
			"family": control.family,
			"default_severity": control.default_severity,
			"automation": control.automation,
			"fetcher_count": len(fetchers),
			"enabled_fetcher_count": len(live),
			"status": _status(fetchers, live),
			"fetchers": ", ".join(fetchers)[:200],
		})

	return _columns(), rows


def _status(fetchers, live):
	if not fetchers:
		return "Not automatable"
	if not live:
		return "Fetcher disabled"
	return "Automated"


def _columns():
	return [
		{"fieldname": "control", "label": _("Control"), "fieldtype": "Link",
		 "options": "NeoGRC Control", "width": 180},
		{"fieldname": "control_id", "label": _("ID"), "fieldtype": "Data", "width": 90},
		{"fieldname": "control_title", "label": _("Title"), "fieldtype": "Data", "width": 260},
		{"fieldname": "family", "label": _("Family"), "fieldtype": "Link",
		 "options": "NeoGRC Control Family", "width": 150},
		{"fieldname": "default_severity", "label": _("Severity"), "fieldtype": "Data", "width": 90},
		{"fieldname": "automation", "label": _("Automation"), "fieldtype": "Data", "width": 130},
		{"fieldname": "status", "label": _("Coverage"), "fieldtype": "Data", "width": 130},
		{"fieldname": "enabled_fetcher_count", "label": _("Live Fetchers"), "fieldtype": "Int",
		 "width": 110},
		{"fieldname": "fetchers", "label": _("Fetchers"), "fieldtype": "Data", "width": 300},
	]
