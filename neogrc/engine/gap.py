# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Gap assessment.

A gap assessment is a join, not an opinion:

    Findings x Crosswalk -> per-control status -> scorecard

Every in-scope control of every target framework starts as ``not_covered``.
Evidence promotes it. This ordering matters: a control with no evidence is a
gap, and the most common failure of automated compliance tooling is to report
absence of evidence as absence of a problem.

Precedence within a control is worst-wins: one failing evaluation makes the
control fail regardless of how many resources pass, because the control is only
as strong as its weakest enforced resource.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, now

from .. import crosswalk
from ..contract import SEVERITY_RANK, SEVERITY_WEIGHT

STATUS_PRECEDENCE = ["fail", "inconclusive", "pass", "not_applicable", "not_covered"]


def enqueue_assessment(assessment: str) -> str:
	doc = frappe.get_doc("NeoGRC Gap Assessment", assessment)
	if doc.docstatus != 0:
		frappe.throw(_("Only a draft assessment can be run"))
	if not doc.frameworks:
		frappe.throw(_("Select at least one target framework"))

	doc.db_set("status", "Queued", update_modified=False)
	frappe.enqueue(
		"neogrc.engine.gap.execute_assessment",
		queue="long",
		timeout=1800,
		job_name=f"grc-gap-{doc.name}",
		enqueue_after_commit=True,
		assessment=doc.name,
	)
	return doc.name


def execute_assessment(assessment: str):
	doc = frappe.get_doc("NeoGRC Gap Assessment", assessment)
	log: list = []
	try:
		doc.db_set("status", "Running", update_modified=False)
		frappe.db.commit()
		_run(doc, log)
		doc.db_set("status", "Completed", update_modified=False)
	except Exception:
		doc.db_set("status", "Failed", update_modified=False)
		log.append("FATAL: " + frappe.get_traceback())
		frappe.log_error(title=f"NeoGRC Gap Assessment {assessment}", message=frappe.get_traceback())
	finally:
		doc.db_set("run_log", "\n".join(log)[-50000:], update_modified=False)
		frappe.db.commit()


# --------------------------------------------------------------------------- #
def _run(doc, log: list):
	frameworks = [row.framework for row in doc.frameworks]
	sources, stale_map = _resolve_sources(doc)
	log.append(f"Frameworks: {', '.join(frameworks)}")
	log.append(f"Sources: {', '.join(sources) if sources else '(none enabled)'}")

	evaluations = _fetch_evaluations(doc, sources, stale_map, log)
	log.append(f"Evaluations in window: {len(evaluations)}")

	# Fan every evaluation out across the frameworks it satisfies.
	coverage: dict = {}
	for ev in evaluations:
		mapping = crosswalk.expand(ev["control_framework"], ev["control_id"], frameworks)
		for fw, entries in mapping.items():
			for control_id, via in entries:
				bucket = coverage.setdefault((fw, control_id), _empty_bucket(via))
				_absorb(bucket, ev)

	results = _build_results(frameworks, coverage, log)
	_write_results(doc, results, sources, stale_map)


def _empty_bucket(via) -> dict:
	return {
		"pass": 0, "fail": 0, "inconclusive": 0, "not_applicable": 0, "skipped": 0,
		"severity": "", "effort": 0.0, "automation": "", "sources": set(), "mapped_via": via,
	}


def _absorb(bucket: dict, ev: dict):
	status = ev["status"]
	if status in bucket:
		bucket[status] += 1
	bucket["sources"].add(ev["source"])

	if status == "fail":
		if SEVERITY_RANK.get(ev.get("severity") or "", 0) > SEVERITY_RANK.get(bucket["severity"], 0):
			bucket["severity"] = ev.get("severity") or ""
		bucket["effort"] += flt(ev.get("effort_hours"))
		if ev.get("automation") and not bucket["automation"]:
			bucket["automation"] = ev["automation"]


def _resolve_sources(doc) -> tuple:
	"""Return the connector list plus a per-connector staleness cutoff."""
	if doc.sources:
		names = [row.connector for row in doc.sources]
	else:
		names = frappe.get_all(
			"NeoGRC Connector", filters={"enabled": 1}, pluck="name", limit_page_length=0
		)

	stale_map = {}
	for name in names:
		ttl = cint(frappe.db.get_value("NeoGRC Connector", name, "cache_ttl_hours")) or 24
		stale_map[name] = add_to_date(now(), hours=-ttl)
	return names, stale_map


def _fetch_evaluations(doc, sources: list, stale_map: dict, log: list) -> list:
	"""Load the most recent evaluation per (source, resource, control)."""
	if not sources:
		return []

	cutoff = add_to_date(doc.as_of or now(), days=-cint(doc.lookback_days or 90))

	rows = frappe.db.sql(
		"""
		SELECT
			f.name AS finding, f.source, f.resource_id, f.collected_at,
			e.control_framework, e.control_id, e.status, e.severity,
			e.effort_hours, e.automation
		FROM `tabNeoGRC Finding` f
		INNER JOIN `tabNeoGRC Finding Evaluation` e ON e.parent = f.name
		WHERE f.source IN %(sources)s
		  AND f.collected_at >= %(cutoff)s
		  AND f.collected_at <= %(as_of)s
		  AND f.disposition NOT IN ('False Positive', 'Suppressed')
		ORDER BY f.collected_at ASC
		""",
		{
			"sources": sources,
			"cutoff": cutoff,
			"as_of": doc.as_of or now(),
		},
		as_dict=True,
	)

	# Later rows overwrite earlier ones, leaving the newest evaluation per key.
	latest: dict = {}
	stale_dropped = 0
	for row in rows:
		if not doc.include_stale:
			threshold = stale_map.get(row.source)
			if threshold and str(row.collected_at) < str(threshold):
				stale_dropped += 1
				continue
		key = (row.source, row.resource_id, row.control_framework, row.control_id)
		latest[key] = row

	if stale_dropped:
		log.append(f"Dropped {stale_dropped} evaluations older than their connector cache TTL")

	return [dict(r) for r in latest.values()]


def _build_results(frameworks: list, coverage: dict, log: list) -> list:
	results = []
	for framework in frameworks:
		controls = frappe.get_all(
			"NeoGRC Control",
			filters={"framework": framework, "is_active": 1},
			fields=["name", "control_id", "control_title", "default_severity",
			        "automation", "effort_hours"],
			limit_page_length=0,
		)
		if not controls:
			log.append(f"WARNING: framework {framework} has no active controls loaded")

		for control in controls:
			bucket = coverage.get((framework, control.control_id))
			if not bucket:
				results.append({
					"framework": framework,
					"control": control.name,
					"control_id": control.control_id,
					"control_title": control.control_title,
					"status": "not_covered",
					"worst_severity": control.default_severity,
					"pass_count": 0, "fail_count": 0,
					"inconclusive_count": 0, "na_count": 0,
					"effort_hours": flt(control.effort_hours),
					"automation": control.automation,
					"evidence_sources": "",
					"mapped_via": "",
				})
				continue

			status = _precedence(bucket)
			results.append({
				"framework": framework,
				"control": control.name,
				"control_id": control.control_id,
				"control_title": control.control_title,
				"status": status,
				"worst_severity": bucket["severity"] or (
					control.default_severity if status == "fail" else "info"
				),
				"pass_count": bucket["pass"],
				"fail_count": bucket["fail"],
				"inconclusive_count": bucket["inconclusive"],
				"na_count": bucket["not_applicable"],
				"effort_hours": bucket["effort"] or (
					flt(control.effort_hours) if status == "fail" else 0
				),
				"automation": bucket["automation"] or control.automation,
				"evidence_sources": ", ".join(sorted(bucket["sources"]))[:140],
				"mapped_via": bucket["mapped_via"] or "",
			})
	return results


def _precedence(bucket: dict) -> str:
	for status in STATUS_PRECEDENCE:
		if bucket.get(status):
			return status
	return "not_covered"


def _write_results(doc, results: list, sources: list, stale_map: dict):
	doc.set("results", [])
	for row in results:
		doc.append("results", row)

	total = len(results)
	passing = sum(1 for r in results if r["status"] == "pass")
	failing = sum(1 for r in results if r["status"] == "fail")
	inconclusive = sum(1 for r in results if r["status"] == "inconclusive")
	not_covered = sum(1 for r in results if r["status"] == "not_covered")
	na = sum(1 for r in results if r["status"] == "not_applicable")

	assessable = total - na
	# Coverage = share of controls any evidence touched at all.
	covered = assessable - not_covered
	# Compliance = share of *assessed* controls that pass. Deliberately excludes
	# uncovered controls so the two numbers cannot mask each other: 100% of 3
	# assessed controls passing is not a compliant programme.
	assessed = passing + failing + inconclusive

	doc.controls_total = total
	doc.controls_pass = passing
	doc.controls_fail = failing
	doc.controls_inconclusive = inconclusive
	doc.controls_not_covered = not_covered
	doc.coverage_pct = (covered / assessable * 100) if assessable else 0
	doc.compliance_pct = (passing / assessed * 100) if assessed else 0
	doc.remediation_effort_hours = sum(flt(r["effort_hours"]) for r in results if r["status"] == "fail")
	doc.weighted_risk_score = sum(
		SEVERITY_WEIGHT.get(r["worst_severity"] or "medium", 3)
		for r in results
		if r["status"] == "fail"
	)

	doc.set("sources", [])
	for name in sources:
		used = sum(1 for r in results if name in (r["evidence_sources"] or ""))
		last_run = frappe.db.get_value("NeoGRC Connector", name, "last_run")
		doc.append("sources", {
			"connector": name,
			"findings_used": used,
			"stale": 1 if (last_run and str(last_run) < str(stale_map.get(name, ""))) else 0,
		})

	doc.flags.ignore_validate_update_after_submit = True
	doc.save(ignore_permissions=True)


# --------------------------------------------------------------------------- #
@frappe.whitelist()
def optimise_multi_framework(assessment: str) -> list:
	"""Rank canonical controls by how many failing framework controls they clear.

	This is the 'implement once, satisfy many' view: a single canonical control
	that resolves nine failures across four frameworks is worth more engineering
	time than nine isolated fixes, even where each individual fix is cheaper.
	"""
	frappe.has_permission("NeoGRC Gap Assessment", doc=assessment, throw=True)
	doc = frappe.get_doc("NeoGRC Gap Assessment", assessment)
	canonical = crosswalk.canonical_framework()

	pivots: dict = {}
	for row in doc.results:
		if row.status not in ("fail", "not_covered"):
			continue
		mapping = crosswalk.expand(row.framework, row.control_id, [canonical])
		for cid, _via in mapping.get(canonical, []):
			entry = pivots.setdefault(cid, {
				"canonical_control": cid,
				"frameworks": set(),
				"controls_resolved": 0,
				"effort_hours": 0.0,
				"weighted_risk": 0,
			})
			entry["frameworks"].add(row.framework)
			entry["controls_resolved"] += 1
			entry["effort_hours"] = max(entry["effort_hours"], flt(row.effort_hours))
			entry["weighted_risk"] += SEVERITY_WEIGHT.get(row.worst_severity or "medium", 3)

	out = []
	for entry in pivots.values():
		entry["frameworks"] = sorted(entry["frameworks"])
		entry["framework_count"] = len(entry["frameworks"])
		effort = entry["effort_hours"] or 1.0
		entry["roi"] = round(entry["weighted_risk"] / effort, 2)
		entry["control_title"] = frappe.db.get_value(
			"NeoGRC Control", f"{canonical}::{entry['canonical_control']}", "control_title"
		)
		out.append(entry)

	return sorted(out, key=lambda e: (-e["roi"], -e["controls_resolved"]))
