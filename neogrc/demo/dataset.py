# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Demo dataset for exercising NeoGRC end to end.

Builds a fictional KSA entity, Wadi Marine Logistics, with a programme that is
deliberately imperfect: some controls pass, some fail, one is only evidenced by
a stale run, one has an approved exception, one has no evidence at all. That mix
is the point. A demo where everything passes tells you nothing about whether the
scoring works, and a green dashboard is exactly the failure mode the gap
assessment is built to expose.

Every record created here is tagged ``neogrc-demo``, and :func:`purge` removes
only records carrying that tag. Nothing touches seeded master data.

    bench --site yoursite execute neogrc.demo.dataset.install
    bench --site yoursite execute neogrc.demo.dataset.purge

Never install this on a production site. :func:`install` refuses to run when
Settings has ``demo_data_blocked`` set.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, now_datetime, nowdate

DEMO_TAG = "neogrc-demo"
COMPANY = "Wadi Marine Logistics"

# Users the dataset assigns ownership to. Created disabled: they exist to make
# owner fields and segregation-of-duties checks meaningful, not to be logged in
# as. Set a password on one manually if you want to test the acknowledgment flow.
DEMO_USERS = [
	("noor.demo@neogrc.test", "Noor", "Al-Harbi", "NeoGRC Manager"),
	("sara.demo@neogrc.test", "Sara", "Idris", "NeoGRC Engineer"),
	("khalid.demo@neogrc.test", "Khalid", "Otaibi", "NeoGRC Auditor"),
]


def _tagged(doc):
	"""Attach the demo tag so purge can find it again."""
	from frappe.desk.doctype.tag.tag import add_tag

	try:
		add_tag(DEMO_TAG, doc.doctype, doc.name)
	except Exception:
		# Tagging is a convenience for purge, not a correctness requirement.
		frappe.log_error(f"Could not tag {doc.doctype} {doc.name}", "NeoGRC demo")


def _insert(doctype: str, payload: dict, tag: bool = True):
	name = payload.get(_autoname_field(doctype))
	if name and frappe.db.exists(doctype, name):
		return frappe.get_doc(doctype, name)
	doc = frappe.new_doc(doctype)
	doc.update(payload)
	doc.flags.ignore_permissions = True
	doc.insert()
	if tag:
		_tagged(doc)
	return doc


def _autoname_field(doctype: str) -> str:
	return {
		"NeoGRC Risk": "risk_id",
		"NeoGRC Exception": "exception_id",
		"NeoGRC Vendor": "vendor_id",
		"NeoGRC Policy": "policy_id",
	}.get(doctype, "name")


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
def create_users():
	created = []
	for email, first, last, role in DEMO_USERS:
		if frappe.db.exists("User", email):
			created.append(email)
			continue
		user = frappe.new_doc("User")
		user.update({
			"email": email,
			"first_name": first,
			"last_name": last,
			"enabled": 0,
			"user_type": "System User",
			"send_welcome_email": 0,
		})
		user.flags.ignore_permissions = True
		user.insert()
		if frappe.db.exists("Role", role):
			user.add_roles(role)
		_tagged(user)
		created.append(email)
	return created


def _user(index: int) -> str:
	email = DEMO_USERS[index][0]
	return email if frappe.db.exists("User", email) else frappe.session.user


# --------------------------------------------------------------------------- #
# Programme records
# --------------------------------------------------------------------------- #
def create_policies():
	"""Three policies: current, overdue, and draft.

	The overdue one exists so EVD-Q-POLICY-REVIEW has something to find. A demo
	set where every policy is current would make that fetcher untestable.
	"""
	out = []

	out.append(_insert("NeoGRC Policy", {
		"policy_id": "DEMO-POL-001",
		"title": "Information Security Policy",
		"status": "approved",
		"version": "2.1",
		"owner_user": _user(0),
		"effective_at": add_days(nowdate(), -120),
		"review_interval_days": 365,
		"document_path": "/private/files/demo/infosec-policy-v2.1.pdf",
		"summary": "Top-level security policy. Current and within review interval.",
		"approvers": [{
			"approver": _user(0),
			"approver_role": "Chief Information Security Officer",
			"approved_on": add_days(nowdate(), -125),
		}],
		"control_refs": [
			{"control_framework": "NCC", "control_id": "GOV-01"},
			{"control_framework": "NCC", "control_id": "GOV-03"},
		],
	}))

	out.append(_insert("NeoGRC Policy", {
		"policy_id": "DEMO-POL-002",
		"title": "Acceptable Use Policy",
		"status": "approved",
		"version": "1.0",
		"owner_user": _user(1),
		"effective_at": add_days(nowdate(), -800),
		"review_interval_days": 365,
		"next_review_at": add_days(nowdate(), -435),
		"document_path": "/private/files/demo/aup-v1.0.pdf",
		"summary": "Deliberately more than a year past its review date.",
		"approvers": [{
			"approver": _user(0),
			"approver_role": "Chief Information Security Officer",
			"approved_on": add_days(nowdate(), -805),
		}],
		"control_refs": [{"control_framework": "NCC", "control_id": "HRS-02"}],
	}))

	out.append(_insert("NeoGRC Policy", {
		"policy_id": "DEMO-POL-003",
		"title": "Cryptographic Key Management Standard",
		"status": "draft",
		"version": "0.3",
		"owner_user": _user(1),
		"summary": "Draft. Should not appear in published-policy evidence.",
		"control_refs": [{"control_framework": "NCC", "control_id": "CRY-02"}],
	}))

	return out


def create_vendors():
	"""Four vendors, one of them the PDPL problem case."""
	out = []

	out.append(_insert("NeoGRC Vendor", {
		"vendor_id": "DEMO-VEN-001",
		"vendor_name": "Gulf Cloud Hosting",
		"tier": "critical",
		"status": "active",
		"owner_user": _user(0),
		"processes_personal_data": 1,
		"dpa_reference": "DPA-2025-014",
		"data_residency": "Kingdom of Saudi Arabia",
		"last_review_at": add_days(nowdate(), -60),
		"review_interval_days": 180,
		"services": [{
			"service_name": "Primary application hosting",
			"data_classification": "Confidential",
			"hosting_region": "KSA Central",
			"is_subprocessor": 0,
		}],
		"contacts": [{
			"contact_name": "Faisal Nasser",
			"role": "Account Manager",
			"email": "faisal@gulfcloud.test",
		}],
	}))

	# This one is the point of the vendor set: personal data, no DPA.
	out.append(_insert("NeoGRC Vendor", {
		"vendor_id": "DEMO-VEN-002",
		"vendor_name": "Northline Payroll Services",
		"tier": "high",
		"status": "active",
		"owner_user": _user(1),
		"processes_personal_data": 1,
		"dpa_reference": "",
		"data_residency": "Ireland",
		"last_review_at": add_days(nowdate(), -400),
		"review_interval_days": 180,
		"services": [{
			"service_name": "Payroll processing",
			"data_classification": "Restricted",
			"hosting_region": "EU West",
			"is_subprocessor": 1,
		}],
		"assurance_evidence": "SOC 2 Type II report received 2024, not yet reviewed.",
	}))

	out.append(_insert("NeoGRC Vendor", {
		"vendor_id": "DEMO-VEN-003",
		"vendor_name": "Riyadh Facilities Management",
		"tier": "low",
		"status": "active",
		"owner_user": _user(1),
		"processes_personal_data": 0,
		"last_review_at": add_days(nowdate(), -30),
		"review_interval_days": 365,
	}))

	out.append(_insert("NeoGRC Vendor", {
		"vendor_id": "DEMO-VEN-004",
		"vendor_name": "Legacy Tape Vault Co",
		"tier": "medium",
		"status": "offboarded",
		"owner_user": _user(0),
		"processes_personal_data": 0,
		"last_review_at": add_days(nowdate(), -700),
	}))

	return out


def create_risks():
	"""Risks spanning all four bands, including one accepted critical residual."""
	out = []

	out.append(_insert("NeoGRC Risk", {
		"risk_id": "DEMO-RSK-001",
		"title": "Unencrypted object storage exposes customer manifests",
		"status": "mitigating",
		"category": "Cybersecurity",
		"statement": "Shipping manifests in object storage lack encryption at rest.",
		"owner_user": _user(0),
		"inherent_likelihood": 4,
		"inherent_impact": 5,
		"residual_likelihood": 2,
		"residual_impact": 5,
		"treatment": "mitigate",
		"treatment_plan": "Enable SSE-KMS across all buckets. Owner: platform team.",
		"target_close_at": add_days(nowdate(), 45),
		"linked_controls": [{"control_framework": "NCC", "control_id": "CRY-01"}],
	}))

	out.append(_insert("NeoGRC Risk", {
		"risk_id": "DEMO-RSK-002",
		"title": "Payroll processor operates without a data processing agreement",
		"status": "open",
		"category": "Privacy",
		"statement": "Personal data of all employees is processed by a vendor with no DPA "
		             "and no confirmed transfer mechanism out of the Kingdom.",
		"owner_user": _user(0),
		"inherent_likelihood": 4,
		"inherent_impact": 4,
		"residual_likelihood": 4,
		"residual_impact": 4,
		"treatment": "mitigate",
		"treatment_plan": "Execute DPA and complete a transfer risk assessment.",
		"target_close_at": add_days(nowdate(), 30),
		"linked_controls": [
			{"control_framework": "NCC", "control_id": "TPR-02"},
			{"control_framework": "NCC", "control_id": "PRI-04"},
		],
	}))

	out.append(_insert("NeoGRC Risk", {
		"risk_id": "DEMO-RSK-003",
		"title": "Legacy VPN appliance past vendor end of support",
		"status": "accepted",
		"category": "Cybersecurity",
		"statement": "Perimeter VPN concentrator no longer receives security patches.",
		"owner_user": _user(0),
		"inherent_likelihood": 3,
		"inherent_impact": 4,
		"residual_likelihood": 3,
		"residual_impact": 4,
		"treatment": "accept",
		"treatment_plan": "Accepted until replacement lands in Q4. See DEMO-EXC-001.",
		"linked_controls": [{"control_framework": "NCC", "control_id": "VUL-02"}],
	}))

	out.append(_insert("NeoGRC Risk", {
		"risk_id": "DEMO-RSK-004",
		"title": "Single site dependency for warehouse operations",
		"status": "watching",
		"category": "Resilience",
		"statement": "No tested failover for the Dammam warehouse management system.",
		"owner_user": _user(1),
		"inherent_likelihood": 2,
		"inherent_impact": 3,
		"residual_likelihood": 2,
		"residual_impact": 3,
		"treatment": "monitor",
		"next_review_at": add_days(nowdate(), -20),
	}))

	return out


def create_exceptions():
	"""One live exception, one expired-but-still-open, one awaiting approval."""
	out = []

	out.append(_insert("NeoGRC Exception", {
		"exception_id": "DEMO-EXC-001",
		"title": "VPN appliance patching deferred pending replacement",
		"status": "approved",
		"owner_user": _user(1),
		"control_framework": "NCC",
		"control_id": "VUL-02",
		"requested_on": add_days(nowdate(), -60),
		"expires_at": add_days(nowdate(), 90),
		"approved_by": _user(0),
		"rationale": "Vendor support ended before the replacement contract was signed. "
		             "Replacement hardware is on order with a confirmed delivery date.",
		"risk_accepted_note": "Accepted by the CISO. Tracked as DEMO-RSK-003.",
		"compensating_controls": [{
			"description": "Management interface restricted to the jump host subnet, "
			               "with session logging retained for 180 days.",
			"effectiveness": "Partial",
		}],
		"linked_risk": "DEMO-RSK-003",
	}))

	# Expired but never closed. EVD-Q-EXCEPTION-EXPIRY should find this one.
	out.append(_insert("NeoGRC Exception", {
		"exception_id": "DEMO-EXC-002",
		"title": "Shared service account retained for legacy EDI integration",
		"status": "approved",
		"owner_user": _user(1),
		"control_framework": "NCC",
		"control_id": "IAM-05",
		"requested_on": add_days(nowdate(), -300),
		"expires_at": add_days(nowdate(), -45),
		"approved_by": _user(0),
		"rationale": "Legacy EDI partner cannot support per-user credentials.",
	}))

	out.append(_insert("NeoGRC Exception", {
		"exception_id": "DEMO-EXC-003",
		"title": "Deferral of MFA for warehouse shop-floor terminals",
		"status": "requested",
		"owner_user": _user(1),
		"control_framework": "NCC",
		"control_id": "IAM-02",
		"requested_on": add_days(nowdate(), -5),
		"expires_at": add_days(nowdate(), 180),
		"rationale": "Shared terminals in a gloved environment cannot use TOTP. "
		             "Proposing badge-tap authentication instead.",
	}))

	return out


# --------------------------------------------------------------------------- #
# Evidence and findings
# --------------------------------------------------------------------------- #
def create_evidence_run(status: str = "Success", days_ago: int = 0):
	run = frappe.new_doc("NeoGRC Evidence Run")
	started = add_to_date(now_datetime(), days=-days_ago)
	run.update({
		"run_id": f"demo-{frappe.generate_hash(length=10)}",
		"status": status,
		"connector": "aws-inspector" if frappe.db.exists("NeoGRC Connector", "aws-inspector") else None,
		"started_at": started,
		"finished_at": add_to_date(started, seconds=42),
		"duration_seconds": 42,
		"trigger_source": "API",
		"fetchers_total": 6,
		"fetchers_succeeded": 6,
		"exit_code": 0,
		"evidence_path": "/tmp/neogrc-demo",
	})
	run.flags.ignore_permissions = True
	run.insert()
	_tagged(run)
	return run


def finding_batch(days_ago: int = 1) -> list:
	"""A contract-valid batch covering pass, fail and inconclusive.

	Mixed on purpose. CRY-01 fails on one bucket and passes on another, which is
	what makes the worst-wins rule visible in the assessment: one unencrypted
	bucket fails the control however many encrypted ones sit beside it.
	"""
	stamp = add_to_date(now_datetime(), days=-days_ago).strftime("%Y-%m-%dT%H:%M:%SZ")
	run_id = f"demo-batch-{days_ago}d"

	def f(rtype, rid, evals, **extra):
		doc = {
			"schema_version": "1.0.0",
			"source": "aws-inspector",
			"source_version": "2026.04.01",
			"run_id": run_id,
			"collected_at": stamp,
			"resource": {"type": rtype, "id": rid, "region": "me-south-1", **extra},
			"evaluations": evals,
		}
		return doc

	def ev(control, status, severity=None, message=None, effort=None, automation=None):
		out = {"control_framework": "NCC", "control_id": control, "status": status}
		if severity:
			out["severity"] = severity
		if message:
			out["message"] = message
		if effort is not None or automation:
			out["remediation"] = {
				"summary": message or "",
				"effort_hours": effort if effort is not None else 2,
				"automation": automation or "semi_automated",
			}
		return out

	return [
		f("aws_s3_bucket", "wadi-prod-manifests", [
			ev("CRY-01", "fail", "high",
			   "Bucket has no default encryption configured", 1, "auto_fixable"),
			ev("DAT-01", "fail", "medium",
			   "Bucket policy permits read from any authenticated principal", 3,
			   "semi_automated"),
		], arn="arn:aws:s3:::wadi-prod-manifests"),

		f("aws_s3_bucket", "wadi-prod-invoices", [
			ev("CRY-01", "pass", "info"),
			ev("DAT-01", "pass", "info"),
		], arn="arn:aws:s3:::wadi-prod-invoices"),

		f("aws_ebs_volume", "vol-0a1b2c3d4e5f", [
			ev("CRY-02", "pass", "info"),
		]),

		f("aws_iam_user", "svc-edi-legacy", [
			ev("IAM-05", "fail", "high",
			   "Service account has console access and no MFA device", 4, "manual"),
			ev("IAM-03", "fail", "medium",
			   "Access key last rotated 612 days ago", 1, "auto_fixable"),
		]),

		f("aws_cloudtrail", "wadi-org-trail", [
			ev("LOG-01", "pass", "info"),
			ev("LOG-02", "inconclusive", message=(
				"AccessDenied calling GetTrailStatus; the collection role lacks "
				"cloudtrail:GetTrailStatus so log delivery could not be confirmed")),
		]),

		f("aws_security_group", "sg-0ffee1234", [
			ev("NET-01", "fail", "critical",
			   "Ingress 0.0.0.0/0 permitted on port 22", 1, "auto_fixable"),
		]),

		f("aws_rds_instance", "wadi-erp-prod", [
			ev("CRY-01", "pass", "info"),
			ev("RES-01", "fail", "high",
			   "Automated backup retention set to 1 day, below the 35 day standard",
			   1, "auto_fixable"),
		]),

		f("aws_kms_key", "arn:aws:kms:me-south-1:key/abcd", [
			ev("CRY-03", "pass", "info"),
			ev("CRY-04", "not_applicable", message="Key rotation is managed by AWS"),
		]),
	]


def invalid_finding_batch() -> list:
	"""Deliberately contract-breaking documents, for negative testing.

	Each one violates exactly one rule so the error message can be checked
	against the specific document index.
	"""
	base = {
		"schema_version": "1.0.0",
		"source": "demo-negative",
		"source_version": "1.0",
		"run_id": "demo-negative",
		"collected_at": "2026-08-01T00:00:00Z",
		"resource": {"type": "test_resource", "id": "res-1"},
	}
	return [
		# 0 - fail with no message
		{**base, "evaluations": [
			{"control_framework": "NCC", "control_id": "CRY-01",
			 "status": "fail", "severity": "high"}]},
		# 1 - fail with no severity
		{**base, "evaluations": [
			{"control_framework": "NCC", "control_id": "CRY-01",
			 "status": "fail", "message": "something is wrong"}]},
		# 2 - inconclusive with no message
		{**base, "evaluations": [
			{"control_framework": "NCC", "control_id": "CRY-01",
			 "status": "inconclusive"}]},
		# 3 - unknown status
		{**base, "evaluations": [
			{"control_framework": "NCC", "control_id": "CRY-01", "status": "green"}]},
		# 4 - no evaluations at all
		{**base, "evaluations": []},
		# 5 - future schema major version
		{**base, "schema_version": "2.0.0", "evaluations": [
			{"control_framework": "NCC", "control_id": "CRY-01", "status": "pass"}]},
		# 6 - negative remediation effort
		{**base, "evaluations": [
			{"control_framework": "NCC", "control_id": "CRY-01", "status": "pass",
			 "remediation": {"effort_hours": -4}}]},
		# 7 - unparseable timestamp
		{**base, "collected_at": "01/08/2026", "evaluations": [
			{"control_framework": "NCC", "control_id": "CRY-01", "status": "pass"}]},
	]


def ingest_demo_findings():
	from neogrc import api

	fresh = api.ingest_findings(findings=json.dumps(finding_batch(days_ago=1)))

	# A second, older batch so staleness is testable. The connector TTL is 24h,
	# so these should be excluded from scoring unless Include Stale is ticked.
	stale = api.ingest_findings(findings=json.dumps(stale_batch()))

	for name in frappe.get_all("NeoGRC Finding",
	                           filters={"run_id": ["like", "demo-%"]}, pluck="name"):
		_tagged(frappe.get_doc("NeoGRC Finding", name))

	return {"fresh": fresh, "stale": stale}


def stale_batch() -> list:
	stamp = add_to_date(now_datetime(), days=-9).strftime("%Y-%m-%dT%H:%M:%SZ")
	return [{
		"schema_version": "1.0.0",
		"source": "k8s-inspector",
		"source_version": "2026.01.01",
		"run_id": "demo-batch-stale",
		"collected_at": stamp,
		"resource": {"type": "k8s_cluster", "id": "wadi-prod-cluster"},
		"evaluations": [
			{"control_framework": "NCC", "control_id": "NET-02", "status": "pass",
			 "severity": "info"},
			{"control_framework": "NCC", "control_id": "CHG-01", "status": "pass",
			 "severity": "info"},
		],
	}]


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def create_metrics():
	"""Twelve weeks of a coverage trend, so the metric report has a shape."""
	out = []
	values = [11, 14, 14, 19, 22, 26, 26, 31, 35, 38, 41, 44]
	for weeks_ago, value in enumerate(reversed(values)):
		doc = frappe.new_doc("NeoGRC Metric")
		doc.update({
			"metric_id": "automation.coverage_pct",
			"label": "Automated control coverage",
			"recorded_at": add_to_date(now_datetime(), weeks=-weeks_ago),
			"value": value,
			"unit": "%",
			"aggregation": "percentage",
			"source": "neogrc-demo",
			"description": "Share of in-scope controls with automated evidence.",
		})
		doc.flags.ignore_permissions = True
		doc.insert()
		_tagged(doc)
		out.append(doc.name)
	return out


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def install():
	"""Build the whole demo dataset. Safe to run more than once."""
	settings = frappe.get_single("NeoGRC Settings")
	if getattr(settings, "demo_data_blocked", 0):
		frappe.throw(_("Demo data is blocked on this site by NeoGRC Settings."))

	summary = {
		"users": create_users(),
		"policies": [d.name for d in create_policies()],
		"vendors": [d.name for d in create_vendors()],
		"risks": [d.name for d in create_risks()],
		"exceptions": [d.name for d in create_exceptions()],
		"metrics": len(create_metrics()),
	}
	summary["findings"] = ingest_demo_findings()
	frappe.db.commit()

	print(json.dumps(summary, indent=2, default=str))
	print(
		"\nDemo data installed and tagged '%s'.\n"
		"Next: create a NeoGRC Gap Assessment scoped to NCC and run it.\n"
		"Remove with: bench --site <site> execute neogrc.demo.dataset.purge"
		% DEMO_TAG
	)
	return summary


DEMO_DOCTYPES = [
	"NeoGRC Finding",
	"NeoGRC Evidence Run",
	"NeoGRC Metric",
	"NeoGRC Exception",
	"NeoGRC Risk",
	"NeoGRC Vendor",
	"NeoGRC Policy Acknowledgment",
	"NeoGRC Policy",
]


def purge(delete_users: int = 0):
	"""Remove everything the demo created. Seeded master data is untouched.

	Order matters: findings and acknowledgments reference risks and policies, so
	they go first. Submitted gap assessments are cancelled before deletion,
	because an audit record should not vanish silently even in a demo.
	"""
	removed = {}

	for name in frappe.get_all("NeoGRC Gap Assessment",
	                           filters={"assessment_title": ["like", "%Demo%"]},
	                           pluck="name"):
		doc = frappe.get_doc("NeoGRC Gap Assessment", name)
		if doc.docstatus == 1:
			doc.cancel()
		doc.delete()
		removed["NeoGRC Gap Assessment"] = removed.get("NeoGRC Gap Assessment", 0) + 1

	for doctype in DEMO_DOCTYPES:
		names = _demo_names(doctype)
		for name in names:
			try:
				frappe.delete_doc(doctype, name, force=1, ignore_permissions=True,
				                  delete_permanently=True)
			except Exception as exc:
				print(f"could not delete {doctype} {name}: {exc}")
		removed[doctype] = len(names)

	if int(delete_users or 0):
		for email, *_ in DEMO_USERS:
			if frappe.db.exists("User", email):
				frappe.delete_doc("User", email, force=1, ignore_permissions=True)
		removed["User"] = len(DEMO_USERS)

	frappe.db.commit()
	print(json.dumps(removed, indent=2))
	return removed


def _demo_names(doctype: str) -> list:
	"""Find demo records by tag, falling back to the DEMO- naming convention."""
	tagged = frappe.get_all(
		"Tag Link",
		filters={"tag": DEMO_TAG, "document_type": doctype},
		pluck="document_name",
	)
	by_name = frappe.get_all(doctype, filters={"name": ["like", "DEMO-%"]}, pluck="name")
	by_run = []
	if doctype in ("NeoGRC Finding", "NeoGRC Evidence Run"):
		by_run = frappe.get_all(doctype, filters={"run_id": ["like", "demo-%"]}, pluck="name")
	by_source = []
	if doctype == "NeoGRC Metric":
		by_source = frappe.get_all(doctype, filters={"source": "neogrc-demo"}, pluck="name")

	return list({*tagged, *by_name, *by_run, *by_source})
