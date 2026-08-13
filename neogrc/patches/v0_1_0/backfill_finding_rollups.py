# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Backfill rollup_status / worst_severity on findings created before rollups existed.

Findings ingested by an early build carry evaluations but no rollup, which makes
them invisible to the list-view filters and to the metric snapshot. Recomputing
in SQL-driven batches keeps this workable on sites with large finding volumes.
"""

import frappe

from neogrc.contract import rollup_status, worst_severity

BATCH = 500


def execute():
	if not frappe.db.has_column("NeoGRC Finding", "rollup_status"):
		return

	names = frappe.get_all(
		"NeoGRC Finding",
		filters={"rollup_status": ("in", ["", None])},
		pluck="name",
		limit_page_length=0,
	)
	if not names:
		return

	for start in range(0, len(names), BATCH):
		chunk = names[start : start + BATCH]
		rows = frappe.get_all(
			"NeoGRC Finding Evaluation",
			filters={"parent": ("in", chunk)},
			fields=["parent", "status", "severity"],
			limit_page_length=0,
		)
		grouped = {}
		for row in rows:
			grouped.setdefault(row.parent, []).append(row)

		for name, evaluations in grouped.items():
			frappe.db.set_value(
				"NeoGRC Finding",
				name,
				{
					"rollup_status": rollup_status(evaluations),
					"worst_severity": worst_severity(evaluations),
				},
				update_modified=False,
			)
		frappe.db.commit()
