# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Declarative evidence from DocTypes already in the site.

Half of a GRC programme's evidence is sitting in the ERP: leavers still holding
roles, purchase orders approved by the person who raised them, assets with no
custodian, training records that expired. Writing a Python fetcher for each of
those is disproportionate work for what is really a query plus a threshold.

This handler lets a consultant declare that check on a Fetcher record instead:
a DocType, a filter set, a condition, and a threshold. No code is deployed and
no Server Script is created.

Adapted in intent from `GRC Evidence Rule` in alphax_grc, with two changes.

That design stores the pass condition as free text - ``IF match.group(1) ==
match.group(2) THEN PASS`` - and evaluates it. Here the condition is an
enumerated Select and the engine dispatches on it, so desk access to a Fetcher
record is not desk access to arbitrary code execution.

Second, filters are parsed as JSON and validated field by field against the
target DocType's own meta before they reach the database. A filter set is
operator-supplied data; without that check it is a query-injection surface into
every table on the site.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now, nowdate

# Conditions the handler understands. Each is a pure comparison against the
# matched row count or a per-row field value.
CONDITIONS = (
	"COUNT_EQUALS_ZERO",        # nothing matched the filters - the usual "no exceptions" check
	"COUNT_LTE_THRESHOLD",
	"COUNT_GTE_THRESHOLD",      # at least N records must exist, e.g. approved policies
	"ALL_ROWS_FIELD_SET",       # every matched row has a non-empty value in field_to_check
	"ALL_ROWS_FIELD_EQUALS",    # every matched row equals expected_value
	"NO_ROW_OLDER_THAN_DAYS",   # freshness: field_to_check within tolerance_days of today
)

# Fieldtypes that must never be pulled into evidence, whatever the operator asks
# for. Evidence artifacts are hashed, attached and retained; a password hash or
# an API secret must not become a permanent audit record.
FORBIDDEN_FIELDTYPES = {"Password"}
FORBIDDEN_FIELDNAMES = {
	"password", "new_password", "api_key", "api_secret", "secret",
	"token", "access_token", "refresh_token", "private_key", "salt",
}

MAX_ROWS = 500


def _meta(doctype: str):
	if not frappe.db.exists("DocType", doctype):
		frappe.throw(_("Evidence query names a DocType that does not exist: {0}").format(doctype))
	return frappe.get_meta(doctype)


def _validate_fields(meta, fieldnames: list) -> list:
	"""Keep only fields that exist on the DocType and are safe to record."""
	valid = {df.fieldname: df for df in meta.fields}
	standard = {"name", "owner", "creation", "modified", "modified_by",
	            "docstatus", "idx", "parent", "parenttype"}
	out = []
	for fieldname in fieldnames:
		if fieldname in standard:
			out.append(fieldname)
			continue
		df = valid.get(fieldname)
		if not df:
			frappe.throw(
				_("Field '{0}' does not exist on {1}").format(fieldname, meta.name)
			)
		if df.fieldtype in FORBIDDEN_FIELDTYPES or fieldname in FORBIDDEN_FIELDNAMES:
			frappe.throw(
				_("Field '{0}' on {1} holds a credential and cannot be used as evidence").format(
					fieldname, meta.name
				)
			)
		out.append(fieldname)
	return out


def _parse_filters(meta, raw) -> dict:
	if not raw or not str(raw).strip():
		return {}
	try:
		filters = json.loads(raw)
	except ValueError as exc:
		frappe.throw(_("Evidence query filters are not valid JSON: {0}").format(exc))
	if not isinstance(filters, dict):
		frappe.throw(_("Evidence query filters must be a JSON object"))

	# Validating the keys is what keeps this from being an open query surface.
	_validate_fields(meta, list(filters.keys()))

	# Resolve the small set of date tokens that make freshness filters expressible
	# without the consultant editing the record every day.
	resolved = {}
	for key, value in filters.items():
		if isinstance(value, list) and len(value) == 2:
			op, operand = value
			if isinstance(operand, str) and operand.startswith("@days_ago:"):
				operand = add_days(nowdate(), -cint(operand.split(":", 1)[1]))
			resolved[key] = [op, operand]
		elif value == "@today":
			resolved[key] = nowdate()
		else:
			resolved[key] = value
	return resolved


def run_query(connector=None, fetcher=None):
	"""Python handler entry point. Registered as the handler on Query fetchers.

	Returns ``(payload, resources)`` so one query can emit a finding per failing
	row rather than a single aggregate - which is what makes the output
	actionable, since "12 leavers still have roles" is a task list, not a status.
	"""
	spec = fetcher.get("query_doctype") and fetcher or None
	if not spec:
		frappe.throw(_("Fetcher {0} has no evidence query configured").format(fetcher.name))

	doctype = fetcher.query_doctype
	meta = _meta(doctype)

	if not frappe.has_permission(doctype, "read"):
		frappe.throw(
			_("The evidence query on {0} reads {1}, which this user cannot access").format(
				fetcher.name, doctype
			),
			frappe.PermissionError,
		)

	filters = _parse_filters(meta, fetcher.query_filters)
	condition = (fetcher.query_condition or "COUNT_EQUALS_ZERO").strip()
	if condition not in CONDITIONS:
		frappe.throw(_("Unknown evidence query condition: {0}").format(condition))

	field = (fetcher.field_to_check or "").strip()
	label_field = meta.get_title_field() or "name"
	fields = _validate_fields(meta, list({"name", label_field, field} - {""}))

	rows = frappe.get_all(
		doctype,
		filters=filters,
		fields=fields,
		limit_page_length=MAX_ROWS + 1,
		ignore_permissions=False,
	)
	truncated = len(rows) > MAX_ROWS
	rows = rows[:MAX_ROWS]

	threshold = cint(fetcher.threshold_value)
	tolerance = cint(fetcher.tolerance_days)
	expected = (fetcher.expected_value or "").strip()

	failing, passed = _evaluate(condition, rows, field, threshold, tolerance, expected)

	payload = {
		"query": {
			"doctype": doctype,
			"filters": filters,
			"condition": condition,
			"field_to_check": field or None,
			"threshold_value": threshold or None,
			"tolerance_days": tolerance or None,
			"expected_value": expected or None,
		},
		"summary": {
			"matched_count": len(rows),
			"failing_count": len(failing),
			"passed": 1 if passed else 0,
			"truncated": 1 if truncated else 0,
		},
		"failing_records": [
			{"name": r.get("name"), "label": r.get(label_field), "value": r.get(field)}
			for r in failing[:50]
		],
		"collected_at": now(),
	}

	# One resource per failing row, so the finding names what has to be fixed.
	resources = [
		{
			"type": frappe.scrub(doctype),
			"id": r.get("name"),
			"name": r.get(label_field) or r.get("name"),
			"attributes": {"field": field, "value": r.get(field)} if field else {},
		}
		for r in failing[:MAX_ROWS]
	]

	if truncated:
		payload["summary"]["note"] = (
			f"More than {MAX_ROWS} records matched; the result is truncated and the "
			"finding understates the position. Narrow the filters."
		)

	return payload, resources


def _evaluate(condition, rows, field, threshold, tolerance, expected):
	"""Return (failing_rows, passed)."""
	if condition == "COUNT_EQUALS_ZERO":
		return rows, not rows

	if condition == "COUNT_LTE_THRESHOLD":
		return (rows if len(rows) > threshold else []), len(rows) <= threshold

	if condition == "COUNT_GTE_THRESHOLD":
		# Nothing to point at when the failure is an absence, so the finding is
		# reported against the query itself rather than a row.
		return [], len(rows) >= threshold

	if condition == "ALL_ROWS_FIELD_SET":
		failing = [r for r in rows if r.get(field) in (None, "", 0)]
		return failing, not failing

	if condition == "ALL_ROWS_FIELD_EQUALS":
		failing = [r for r in rows if str(r.get(field) or "") != expected]
		return failing, not failing

	if condition == "NO_ROW_OLDER_THAN_DAYS":
		cutoff = getdate(add_days(nowdate(), -tolerance))
		failing = []
		for r in rows:
			value = r.get(field)
			if not value:
				failing.append(r)
				continue
			try:
				if getdate(value) < cutoff:
					failing.append(r)
			except Exception:
				failing.append(r)
		return failing, not failing

	frappe.throw(_("Unhandled condition {0}").format(condition))
	return [], False


def preview(fetcher_name: str) -> dict:
	"""Run the query without recording anything. Used by the Fetcher form."""
	fetcher = frappe.get_doc("NeoGRC Evidence Fetcher", fetcher_name)
	payload, resources = run_query(connector=None, fetcher=fetcher)
	return {"payload": payload, "resource_count": len(resources)}
