# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Finding data contract.

Implements ``schemas/finding.schema.json`` v1.0.0 from the NeoGRC Engineering
reference toolkit, without pulling in a JSON-Schema runtime. Every connector,
whether it runs inside this app or ships a payload to
``/api/method/neogrc.api.ingest_findings``, must satisfy this
contract before a NeoGRC Finding record is created.

Keeping validation here (rather than in the DocType controller) means the same
rules apply to bulk API ingestion, the internal evidence runner and the fixture
loader.
"""

from __future__ import annotations

import datetime
import re

import frappe
from frappe import _

SCHEMA_VERSION = "1.0.0"

EVAL_STATUSES = ("pass", "fail", "not_applicable", "inconclusive", "skipped")
SEVERITIES = ("critical", "high", "medium", "low", "info")
AUTOMATION_LEVELS = ("auto_fixable", "semi_automated", "manual", "design_change")

#: Severity weights used across scoring, ordering and rollups.
SEVERITY_WEIGHT = {"critical": 10, "high": 6, "medium": 3, "low": 1, "info": 0}
SEVERITY_RANK = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}

#: Structured exit codes, mirroring the connector quality bar.
EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_AUTH = 2
EXIT_RATE_LIMITED = 3
EXIT_PARTIAL = 4
EXIT_NOT_INSTALLED = 5

EXIT_STATUS = {
	EXIT_OK: "Success",
	EXIT_GENERIC: "Failed",
	EXIT_AUTH: "Auth Error",
	EXIT_RATE_LIMITED: "Rate Limited",
	EXIT_PARTIAL: "Partial",
	EXIT_NOT_INSTALLED: "Not Installed",
}

_ISO = re.compile(
	r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$"
)


class ContractError(frappe.ValidationError):
	"""Raised when a payload does not satisfy the Finding contract."""


def _err(path: str, message: str) -> str:
	return f"{path}: {message}"


def _require(obj: dict, key: str, path: str, errors: list) -> bool:
	value = obj.get(key)
	if value is None or (isinstance(value, str) and not value.strip()):
		errors.append(_err(f"{path}.{key}", _("is required")))
		return False
	return True


def _check_enum(value, allowed, path: str, errors: list, required=False):
	if value in (None, ""):
		if required:
			errors.append(_err(path, _("is required")))
		return
	if value not in allowed:
		errors.append(_err(path, _("must be one of {0}").format(", ".join(allowed))))


def _check_timestamp(value, path: str, errors: list):
	if isinstance(value, (datetime.datetime, datetime.date)):
		return
	if not isinstance(value, str) or not _ISO.match(value.strip()):
		errors.append(_err(path, _("must be an ISO-8601 date-time")))


def normalise_timestamp(value) -> str:
	"""Return a Frappe-storable naive UTC datetime string."""
	if isinstance(value, datetime.datetime):
		dt = value
	elif isinstance(value, datetime.date):
		dt = datetime.datetime(value.year, value.month, value.day)
	else:
		text = str(value or "").strip().replace("Z", "+00:00").replace(" ", "T", 1)
		try:
			dt = datetime.datetime.fromisoformat(text)
		except ValueError:
			return frappe.utils.now()
	if dt.tzinfo is not None:
		dt = dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
	return dt.strftime("%Y-%m-%d %H:%M:%S")


def validate_finding(payload: dict, index: int = 0) -> list:
	"""Validate one Finding document. Returns a list of human-readable errors."""
	errors: list = []
	path = f"findings[{index}]"

	if not isinstance(payload, dict):
		return [_err(path, _("must be an object"))]

	for key in ("schema_version", "source", "source_version", "run_id", "collected_at"):
		_require(payload, key, path, errors)

	if payload.get("schema_version") and payload["schema_version"].split(".")[0] != "1":
		errors.append(
			_err(
				f"{path}.schema_version",
				_("unsupported major version; this app implements {0}").format(SCHEMA_VERSION),
			)
		)

	if payload.get("collected_at"):
		_check_timestamp(payload["collected_at"], f"{path}.collected_at", errors)

	resource = payload.get("resource")
	if not isinstance(resource, dict):
		errors.append(_err(f"{path}.resource", _("is required and must be an object")))
	else:
		_require(resource, "type", f"{path}.resource", errors)
		_require(resource, "id", f"{path}.resource", errors)
		tags = resource.get("tags")
		if tags is not None and not isinstance(tags, dict):
			errors.append(_err(f"{path}.resource.tags", _("must be an object")))

	evaluations = payload.get("evaluations")
	if not isinstance(evaluations, list) or not evaluations:
		errors.append(_err(f"{path}.evaluations", _("at least one evaluation is required")))
	else:
		for i, ev in enumerate(evaluations):
			errors.extend(_validate_evaluation(ev, f"{path}.evaluations[{i}]"))

	for i, nf in enumerate(payload.get("findings") or []):
		npath = f"{path}.findings[{i}]"
		if not isinstance(nf, dict):
			errors.append(_err(npath, _("must be an object")))
			continue
		for key in ("id", "title", "severity"):
			_require(nf, key, npath, errors)
		_check_enum(nf.get("severity"), SEVERITIES, f"{npath}.severity", errors)

	return errors


def _validate_evaluation(ev, path: str) -> list:
	errors: list = []
	if not isinstance(ev, dict):
		return [_err(path, _("must be an object"))]

	_require(ev, "control_framework", path, errors)
	_require(ev, "control_id", path, errors)
	_check_enum(ev.get("status"), EVAL_STATUSES, f"{path}.status", errors, required=True)
	_check_enum(ev.get("severity"), SEVERITIES, f"{path}.severity", errors)

	status = ev.get("status")

	# The contract's conditional requirements: a failing evaluation without a
	# message and severity is not auditable, and an inconclusive one without a
	# message cannot be triaged.
	if status == "fail":
		if not (ev.get("message") or "").strip():
			errors.append(_err(f"{path}.message", _("is required when status is 'fail'")))
		if not ev.get("severity"):
			errors.append(_err(f"{path}.severity", _("is required when status is 'fail'")))
	elif status == "inconclusive":
		if not (ev.get("message") or "").strip():
			errors.append(_err(f"{path}.message", _("is required when status is 'inconclusive'")))

	remediation = ev.get("remediation")
	if remediation not in (None, {}):
		if not isinstance(remediation, dict):
			errors.append(_err(f"{path}.remediation", _("must be an object")))
		else:
			_check_enum(
				remediation.get("automation"), AUTOMATION_LEVELS,
				f"{path}.remediation.automation", errors,
			)
			effort = remediation.get("effort_hours")
			if effort is not None:
				try:
					if float(effort) < 0:
						raise ValueError
				except (TypeError, ValueError):
					errors.append(
						_err(f"{path}.remediation.effort_hours", _("must be a number >= 0"))
					)

	refs = ev.get("evidence_refs")
	if refs is not None and not isinstance(refs, list):
		errors.append(_err(f"{path}.evidence_refs", _("must be an array of strings")))

	if ev.get("assessed_at"):
		_check_timestamp(ev["assessed_at"], f"{path}.assessed_at", errors)

	return errors


def validate_batch(payloads: list) -> list:
	"""Validate a list of Finding documents, returning every error found."""
	errors: list = []
	if not isinstance(payloads, list):
		return [_("payload must be a list of Finding documents")]
	for i, payload in enumerate(payloads):
		errors.extend(validate_finding(payload, i))
	return errors


def assert_valid(payloads: list):
	errors = validate_batch(payloads)
	if errors:
		frappe.throw(
			_("Finding contract validation failed:") + "\n" + "\n".join(errors[:40]),
			ContractError,
			title=_("Invalid Finding Payload"),
		)


def rollup_status(evaluations) -> str:
	"""Worst-wins rollup across a finding's evaluations."""
	statuses = {(e.get("status") if isinstance(e, dict) else e.status) for e in evaluations}
	for candidate in ("fail", "inconclusive", "pass", "skipped", "not_applicable"):
		if candidate in statuses:
			return candidate
	return "not_applicable"


def worst_severity(evaluations) -> str | None:
	"""Highest severity among *failing* evaluations, else the highest overall."""
	def sev(e):
		return (e.get("severity") if isinstance(e, dict) else e.severity) or ""

	def status(e):
		return (e.get("status") if isinstance(e, dict) else e.status) or ""

	failing = [sev(e) for e in evaluations if status(e) == "fail" and sev(e)]
	pool = failing or [sev(e) for e in evaluations if sev(e)]
	if not pool:
		return None
	return max(pool, key=lambda s: SEVERITY_RANK.get(s, 0))
