# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Scheduled jobs.

All of these are cheap sweeps that either enqueue work or write small records.
None of them do the heavy lifting inline: evidence collection is dispatched to
the long queue by the runner, so a slow AWS account cannot stall the scheduler.
"""

from __future__ import annotations

import json
import os
import shutil

import frappe
from frappe.utils import add_days, add_to_date, cint, getdate, now, nowdate

from ..engine import evidence_runner

INTERVAL_DAYS = {"Daily": 1, "Weekly": 7, "Monthly": 30, "Quarterly": 91}


def run_due_evidence_sets():
	"""Queue any enabled Evidence Set whose next run date has arrived."""
	if not frappe.db.get_single_value("NeoGRC Settings", "enabled"):
		return

	due = frappe.get_all(
		"NeoGRC Evidence Set",
		filters={
			"enabled": 1,
			"schedule": ("!=", "Manual"),
			"next_run_on": ("<=", nowdate()),
		},
		fields=["name", "schedule"],
		limit_page_length=0,
	)

	for row in due:
		# Skip if a run for this set is still in flight; overlapping runs on the
		# same cloud account produce throttling, not more evidence.
		in_flight = frappe.db.exists(
			"NeoGRC Evidence Run",
			{"evidence_set": row.name, "status": ("in", ["Queued", "Running"])},
		)
		if in_flight:
			frappe.logger("neogrc").info(
				f"Skipping {row.name}: run {in_flight} still in flight"
			)
			continue

		try:
			evidence_runner.enqueue_evidence_set(row.name, "Scheduler")
			frappe.db.set_value(
				"NeoGRC Evidence Set",
				row.name,
				"next_run_on",
				add_days(nowdate(), INTERVAL_DAYS.get(row.schedule, 30)),
				update_modified=False,
			)
		except Exception:
			frappe.log_error(
				title=f"NeoGRC scheduled evidence set {row.name}",
				message=frappe.get_traceback(),
			)

	frappe.db.commit()


def expire_exceptions():
	"""Flip approved exceptions past their expiry date to 'expired'.

	An exception that has quietly outlived its approval is the single most
	common audit finding in a running GRC programme, so this runs daily rather
	than waiting for someone to open the record.
	"""
	expired = frappe.get_all(
		"NeoGRC Exception",
		filters={"status": "approved", "expires_at": ("<", nowdate())},
		fields=["name", "title", "owner_user"],
		limit_page_length=0,
	)

	for row in expired:
		frappe.db.set_value("NeoGRC Exception", row.name, "status", "expired", update_modified=False)
		_notify(
			row.owner_user,
			f"NeoGRC exception {row.name} has expired",
			f"'{row.title}' passed its expiry date and has been marked expired. "
			"Either close it or raise a fresh, approved exception.",
			"NeoGRC Exception",
			row.name,
		)

	frappe.db.commit()


def flag_overdue_reviews():
	"""Notify owners of overdue risk, policy and vendor reviews."""
	today = nowdate()

	for doctype, date_field, owner_field, label in (
		("NeoGRC Risk", "next_review_at", "owner_user", "risk"),
		("NeoGRC Policy", "next_review_at", "owner_user", "policy"),
		("NeoGRC Vendor", "next_review_at", "owner_user", "vendor"),
	):
		filters = {date_field: ("<", today)}
		if doctype == "NeoGRC Policy":
			filters["status"] = "approved"
		elif doctype == "NeoGRC Vendor":
			filters["status"] = "active"
		else:
			filters["status"] = ("not in", ["closed", "accepted"])

		overdue = frappe.get_all(
			doctype, filters=filters, fields=["name", owner_field], limit_page_length=0
		)
		for row in overdue:
			_notify(
				row.get(owner_field),
				f"Overdue {label} review: {row.name}",
				f"The scheduled review date for {row.name} has passed.",
				doctype,
				row.name,
			)

	frappe.db.commit()


def snapshot_metrics():
	"""Write the daily KPI/KRI row set.

	Metric IDs follow the reporting conventions from the reference toolkit so
	dashboards built against either system read the same keys.
	"""
	from ..api import control_coverage

	stamp = now()
	rows = []

	for severity in ("critical", "high", "medium", "low"):
		rows.append((
			f"findings.open_{severity}",
			frappe.db.count(
				"NeoGRC Finding",
				{
					"rollup_status": "fail",
					"worst_severity": severity,
					"disposition": ("in", ["Open", "Acknowledged"]),
				},
			),
			"count",
			{},
		))

	rows.append((
		"risk.residual_total",
		sum(
			cint(r.residual_score or r.inherent_score)
			for r in frappe.get_all(
				"NeoGRC Risk",
				filters={"status": ("not in", ["closed"])},
				fields=["residual_score", "inherent_score"],
				limit_page_length=0,
			)
		),
		"count",
		{},
	))

	rows.append((
		"policy.review_overdue",
		frappe.db.count(
			"NeoGRC Policy", {"status": "approved", "next_review_at": ("<", nowdate())}
		),
		"count",
		{},
	))

	rows.append((
		"exception.open_expired",
		frappe.db.count("NeoGRC Exception", {"status": "expired"}),
		"count",
		{},
	))

	# Automation coverage per active framework.
	for framework in frappe.get_all(
		"NeoGRC Framework", filters={"is_active": 1}, pluck="name", limit_page_length=0
	):
		try:
			coverage = control_coverage(framework)
		except Exception:
			continue
		if not coverage["total"]:
			continue
		dims = {"framework": framework}
		rows.append(("automation.coverage_pct", coverage["coverage_pct"], "percentage", dims))
		rows.append(("automation.controls_automated", coverage["automatable"], "count", dims))
		rows.append((
			"automation.controls_manual",
			coverage["total"] - coverage["automatable"], "count", dims,
		))
		rows.append(("automation.controls_total", coverage["total"], "count", dims))

	for metric_id, value, aggregation, dimensions in rows:
		doc = frappe.new_doc("NeoGRC Metric")
		doc.metric_id = metric_id
		doc.value = value
		doc.aggregation = aggregation
		doc.recorded_at = stamp
		doc.source = "scheduler"
		doc.unit = "%" if aggregation == "percentage" else None
		if dimensions:
			doc.dimensions = json.dumps(dimensions)
		doc.insert(ignore_permissions=True)

	frappe.db.commit()


def purge_expired_artifacts():
	"""Delete evidence artifacts past the retention window.

	Deliberately conservative: artifacts attached to a *submitted* gap
	assessment are kept regardless of age, because deleting the evidence
	underneath a signed-off assessment breaks the audit trail it depends on.
	"""
	settings = frappe.get_cached_doc("NeoGRC Settings")
	retention = cint(settings.artifact_retention_days)
	if retention <= 0:
		return

	cutoff = add_to_date(now(), days=-retention)
	protected_runs = set(
		frappe.db.sql_list(
			"""
			SELECT DISTINCT f.evidence_run
			FROM `tabNeoGRC Finding` f
			WHERE f.evidence_run IS NOT NULL
			  AND f.run_id IN (
				SELECT DISTINCT ff.run_id FROM `tabNeoGRC Finding` ff
				WHERE ff.name IN (
					SELECT DISTINCT r.control FROM `tabNeoGRC Gap Assessment Result` r
					INNER JOIN `tabNeoGRC Gap Assessment` a ON a.name = r.parent
					WHERE a.docstatus = 1
				)
			  )
			"""
		)
	)

	stale = frappe.get_all(
		"NeoGRC Evidence Artifact",
		filters={"collected_at": ("<", cutoff)},
		fields=["name", "file_path", "evidence_run"],
		limit_page_length=500,
	)

	removed = 0
	for row in stale:
		if row.evidence_run in protected_runs:
			continue
		if row.file_path and os.path.isfile(row.file_path):
			try:
				os.remove(row.file_path)
			except OSError:
				pass
		frappe.delete_doc("NeoGRC Evidence Artifact", row.name, force=1, ignore_permissions=True)
		removed += 1

	_purge_empty_run_dirs(settings)

	if removed:
		frappe.logger("neogrc").info(f"Purged {removed} evidence artifacts past retention")
	frappe.db.commit()


def _purge_empty_run_dirs(settings):
	base = settings.evidence_base_path
	if not base or not os.path.isdir(base):
		return
	site_dir = os.path.join(base, frappe.local.site)
	if not os.path.isdir(site_dir):
		return
	for entry in os.listdir(site_dir):
		path = os.path.join(site_dir, entry)
		if os.path.isdir(path) and not os.listdir(path):
			shutil.rmtree(path, ignore_errors=True)


def _notify(user: str | None, subject: str, message: str, doctype: str, name: str):
	if not user:
		return
	try:
		frappe.get_doc({
			"doctype": "Notification Log",
			"for_user": user,
			"type": "Alert",
			"document_type": doctype,
			"document_name": name,
			"subject": subject,
			"email_content": message,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="NeoGRC notification", message=frappe.get_traceback())
