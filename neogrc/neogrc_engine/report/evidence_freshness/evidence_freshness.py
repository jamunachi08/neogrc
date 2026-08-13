# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, now, time_diff_in_hours


def execute(filters=None):
	filters = frappe._dict(filters or {})
	fetcher_filters = {}
	if filters.get("connector"):
		fetcher_filters["connector"] = filters.connector
	if not filters.get("include_disabled"):
		fetcher_filters["enabled"] = 1

	fetchers = frappe.get_all(
		"NeoGRC Evidence Fetcher",
		filters=fetcher_filters,
		fields=["name", "fetcher_name", "connector", "enabled", "last_run", "last_status"],
		order_by="connector asc, name asc",
		limit_page_length=0,
	)

	ttl_by_connector = {
		c.name: cint(c.cache_ttl_hours) or 24
		for c in frappe.get_all("NeoGRC Connector", fields=["name", "cache_ttl_hours"])
	}

	rows = []
	for fetcher in fetchers:
		ttl = ttl_by_connector.get(fetcher.connector, 24)
		age = time_diff_in_hours(now(), fetcher.last_run) if fetcher.last_run else None
		rows.append({
			**fetcher,
			"cache_ttl_hours": ttl,
			"age_hours": round(age, 1) if age is not None else None,
			"freshness": _freshness(fetcher.last_run, age, ttl),
			"controls": frappe.db.count("NeoGRC Fetcher Control Link", {"parent": fetcher.name}),
		})

	rank = {"Never run": 0, "Stale": 1, "Ageing": 2, "Fresh": 3}
	rows.sort(key=lambda r: rank.get(r["freshness"], 9))
	return _columns(), rows


def _freshness(last_run, age, ttl):
	if not last_run:
		return "Never run"
	if age > ttl:
		return "Stale"
	if age > ttl * 0.75:
		return "Ageing"
	return "Fresh"


def _columns():
	return [
		{"fieldname": "name", "label": _("Fetcher"), "fieldtype": "Link",
		 "options": "NeoGRC Evidence Fetcher", "width": 180},
		{"fieldname": "fetcher_name", "label": _("Name"), "fieldtype": "Data", "width": 240},
		{"fieldname": "connector", "label": _("Connector"), "fieldtype": "Link",
		 "options": "NeoGRC Connector", "width": 150},
		{"fieldname": "enabled", "label": _("Enabled"), "fieldtype": "Check", "width": 80},
		{"fieldname": "freshness", "label": _("Freshness"), "fieldtype": "Data", "width": 110},
		{"fieldname": "age_hours", "label": _("Age (h)"), "fieldtype": "Float", "width": 90},
		{"fieldname": "cache_ttl_hours", "label": _("TTL (h)"), "fieldtype": "Int", "width": 80},
		{"fieldname": "last_status", "label": _("Last Status"), "fieldtype": "Data", "width": 120},
		{"fieldname": "controls", "label": _("Controls"), "fieldtype": "Int", "width": 90},
	]
