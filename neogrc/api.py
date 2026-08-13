# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Public API surface.

Every endpoint here is role-guarded. Ingestion is the one that matters most:
it is how an external connector - a GitHub Action, a Wiz export, a Paramify
fetcher run on a jump host - gets evidence into the register without anyone
touching the desk UI.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now

from . import contract, crosswalk
from .engine import evidence_runner, gap

WRITE_ROLES = ("NeoGRC Manager", "NeoGRC Engineer", "System Manager")
READ_ROLES = ("NeoGRC Manager", "NeoGRC Engineer", "NeoGRC Auditor", "System Manager")

MAX_BATCH = 2000


def _guard(roles=WRITE_ROLES):
	if frappe.session.user == "Guest":
		raise frappe.PermissionError(_("Authentication required"))
	if not set(roles) & set(frappe.get_roles()):
		frappe.throw(
			_("You need one of these roles to perform this action: {0}").format(", ".join(roles)),
			frappe.PermissionError,
		)


def _parse(payload):
	if isinstance(payload, str):
		try:
			return json.loads(payload)
		except ValueError:
			frappe.throw(_("Payload is not valid JSON"))
	return payload


# --------------------------------------------------------------------------- #
# Ingestion
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def ingest_findings(findings=None, dry_run: int = 0) -> dict:
	"""Ingest a batch of contract-conformant Finding documents.

	The whole batch is validated before anything is written. A partially
	ingested run is worse than a rejected one, because a gap assessment over
	half a connector's output silently reports controls as uncovered.
	"""
	_guard()
	payload = _parse(findings)
	if isinstance(payload, dict):
		payload = payload.get("findings", [payload])
	if not isinstance(payload, list):
		frappe.throw(_("Expected a list of Finding documents"))
	if len(payload) > MAX_BATCH:
		frappe.throw(_("Batch too large: {0} findings, maximum is {1}").format(len(payload), MAX_BATCH))

	errors = contract.validate_batch(payload)
	if errors:
		return {"ok": False, "created": 0, "errors": errors[:50], "error_count": len(errors)}

	if cint(dry_run):
		return {"ok": True, "created": 0, "dry_run": True, "validated": len(payload)}

	created, skipped = [], 0
	for item in payload:
		name = _persist(item)
		if name:
			created.append(name)
		else:
			skipped += 1

	frappe.db.commit()
	return {
		"ok": True,
		"created": len(created),
		"skipped": skipped,
		"names": created[:200],
		"errors": [],
	}


def _persist(item: dict) -> str | None:
	resource = item.get("resource") or {}

	# Idempotency: a connector re-posting the same run must not duplicate.
	existing = frappe.db.exists(
		"NeoGRC Finding",
		{
			"run_id": item["run_id"],
			"source": item["source"],
			"resource_id": str(resource.get("id"))[:140],
			"resource_type": resource.get("type"),
		},
	)
	if existing:
		return None

	settings = frappe.get_cached_doc("NeoGRC Settings")

	doc = frappe.new_doc("NeoGRC Finding")
	doc.schema_version = item.get("schema_version") or contract.SCHEMA_VERSION
	doc.source = item["source"]
	doc.source_version = item["source_version"]
	doc.run_id = item["run_id"]
	doc.collected_at = contract.normalise_timestamp(item["collected_at"])
	doc.resource_type = resource.get("type")
	doc.resource_id = str(resource.get("id"))[:140]
	doc.resource_arn = resource.get("arn")
	doc.resource_uri = resource.get("uri")
	doc.region = resource.get("region")
	doc.account_id = resource.get("account_id")

	if resource.get("tags"):
		doc.resource_tags = json.dumps(resource["tags"], default=str)

	raw = item.get("raw_attributes")
	if raw:
		if settings.redact_raw_attributes:
			raw = _redact(raw, settings)
		doc.raw_attributes = json.dumps(raw, indent=1, default=str)[:200000]

	if item.get("metadata"):
		doc.metadata = json.dumps(item["metadata"], default=str)

	for ev in item["evaluations"]:
		remediation = ev.get("remediation") or {}
		doc.append("evaluations", {
			"control_framework": ev["control_framework"],
			"control_id": ev["control_id"],
			"control": crosswalk.resolve_control(ev["control_framework"], ev["control_id"]),
			"status": ev["status"],
			"severity": ev.get("severity"),
			"message": (ev.get("message") or "")[:1000],
			"remediation_summary": remediation.get("summary"),
			"remediation_ref": remediation.get("ref"),
			"effort_hours": remediation.get("effort_hours"),
			"automation": remediation.get("automation"),
			"evidence_refs": "\n".join(ev.get("evidence_refs") or []),
			"assessed_at": contract.normalise_timestamp(ev["assessed_at"])
			if ev.get("assessed_at")
			else None,
		})

	doc.insert(ignore_permissions=True)

	for nf in item.get("findings") or []:
		_persist_narrative(nf, item)

	return doc.name


def _persist_narrative(nf: dict, item: dict):
	if frappe.db.exists("NeoGRC Narrative Finding", {"run_id": item["run_id"], "title": nf["title"]}):
		return
	doc = frappe.new_doc("NeoGRC Narrative Finding")
	doc.title = nf["title"][:140]
	doc.severity = nf["severity"]
	doc.description = nf.get("description")
	doc.source = item["source"]
	doc.run_id = item["run_id"]
	doc.related_resources = "\n".join(nf.get("related_resource_ids") or [])
	for cid in nf.get("related_control_ids") or []:
		doc.append("related_controls", {"control_id": cid})
	doc.insert(ignore_permissions=True)


def _redact(raw, settings):
	keys = {k.strip().lower() for k in (settings.redaction_keys or "").splitlines() if k.strip()}
	if not keys:
		return raw

	def walk(node):
		if isinstance(node, dict):
			return {
				k: ("[REDACTED]" if any(key in k.lower() for key in keys) else walk(v))
				for k, v in node.items()
			}
		if isinstance(node, list):
			return [walk(v) for v in node]
		return node

	return walk(raw)


# --------------------------------------------------------------------------- #
# Operations
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def run_evidence_set(evidence_set: str, trigger_source: str = "API") -> dict:
	_guard()
	frappe.has_permission("NeoGRC Evidence Set", doc=evidence_set, throw=True)
	run = evidence_runner.enqueue_evidence_set(evidence_set, trigger_source)
	return {"ok": True, "evidence_run": run}


@frappe.whitelist()
def run_gap_assessment(assessment: str) -> dict:
	_guard()
	frappe.has_permission("NeoGRC Gap Assessment", doc=assessment, throw=True)
	gap.enqueue_assessment(assessment)
	return {"ok": True, "assessment": assessment, "status": "Queued"}


@frappe.whitelist()
def pipeline_status() -> dict:
	"""Connector health: last run, freshness, failure streak, finding volume."""
	_guard(READ_ROLES)
	rows = frappe.get_all(
		"NeoGRC Connector",
		fields=[
			"name", "connector_name", "category", "enabled", "last_run",
			"last_status", "consecutive_failures", "cache_ttl_hours", "source_version",
		],
		limit_page_length=0,
		order_by="category asc, name asc",
	)

	from frappe.utils import add_to_date, time_diff_in_hours

	out = []
	for row in rows:
		age_hours = time_diff_in_hours(now(), row.last_run) if row.last_run else None
		out.append({
			**row,
			"age_hours": round(age_hours, 1) if age_hours is not None else None,
			"stale": bool(age_hours is not None and age_hours > (row.cache_ttl_hours or 24)),
			"never_run": not row.last_run,
			"findings_7d": frappe.db.count(
				"NeoGRC Finding",
				{"source": row.name, "collected_at": (">", add_to_date(now(), days=-7))},
			),
		})

	return {
		"connectors": out,
		"healthy": sum(1 for c in out if c["last_status"] == "Success" and not c["stale"]),
		"total": len(out),
		"generated_at": now(),
	}


@frappe.whitelist()
def control_coverage(framework: str) -> dict:
	"""Which controls of a framework any connector can currently evidence."""
	_guard(READ_ROLES)
	controls = frappe.get_all(
		"NeoGRC Control",
		filters={"framework": framework, "is_active": 1},
		fields=["control_id", "control_title", "automation"],
		limit_page_length=0,
	)

	links = frappe.get_all(
		"NeoGRC Fetcher Control Link",
		fields=["control_framework", "control_id", "parent"],
		limit_page_length=0,
	)

	covered: dict = {}
	for link in links:
		mapping = crosswalk.expand(link.control_framework, link.control_id, [framework])
		for cid, _via in mapping.get(framework, []):
			covered.setdefault(cid, set()).add(link.parent)

	rows = [
		{
			**c,
			"fetchers": sorted(covered.get(c.control_id, [])),
			"automatable": bool(covered.get(c.control_id)),
		}
		for c in controls
	]

	automatable = sum(1 for r in rows if r["automatable"])
	return {
		"framework": framework,
		"controls": rows,
		"total": len(rows),
		"automatable": automatable,
		"coverage_pct": round(automatable / len(rows) * 100, 1) if rows else 0,
	}


@frappe.whitelist()
def map_control(framework: str, control_id: str, target_frameworks: str | None = None) -> dict:
	_guard(READ_ROLES)
	return crosswalk.map_control(framework, control_id, target_frameworks)


@frappe.whitelist()
def record_metric(metric_id: str, value: float, dimensions=None, unit=None,
                  aggregation: str = "gauge", source: str | None = None) -> str:
	_guard()
	doc = frappe.new_doc("NeoGRC Metric")
	doc.metric_id = metric_id
	doc.value = value
	doc.unit = unit
	doc.aggregation = aggregation
	doc.source = source
	doc.recorded_at = now()
	if dimensions:
		doc.dimensions = json.dumps(_parse(dimensions), default=str)
	doc.insert(ignore_permissions=True)
	return doc.name
