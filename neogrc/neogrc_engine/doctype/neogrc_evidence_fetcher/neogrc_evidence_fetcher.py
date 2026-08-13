# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

import os
import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from neogrc import crosswalk


class NeoGRCEvidenceFetcher(Document):
	def validate(self):
		self.validate_handler()
		self.validate_rules()
		self.resolve_controls()

	def validate_handler(self):
		connector = frappe.get_cached_doc("NeoGRC Connector", self.connector)

		if self.handler_mode == "Query" or connector.handler_type == "Internal Query":
			self.validate_query()
			return

		if connector.handler_type == "Python":
			if not re.match(r"^[\w.]+$", self.handler or ""):
				frappe.throw(_("A Python handler must be a dotted module path"))
			return

		if os.path.isabs(self.handler or ""):
			frappe.throw(
				_("Shell handlers must be relative to the connector script root, not absolute paths")
			)
		if ".." in (self.handler or "").split("/"):
			frappe.throw(_("Shell handlers may not traverse outside the connector script root"))

	def validate_query(self):
		"""Validate a declarative query at save time, not at run time.

		A query that names a missing field or an unparseable filter set should
		fail in front of the person writing it, not silently at 2am inside a
		scheduled run where it would surface as an unexplained collection fault.
		"""
		import json

		from neogrc.engine.query_fetcher import (
			CONDITIONS, FORBIDDEN_FIELDNAMES, FORBIDDEN_FIELDTYPES,
		)

		if not self.query_doctype:
			frappe.throw(_("A query fetcher needs a source DocType"))
		if not frappe.db.exists("DocType", self.query_doctype):
			frappe.throw(_("DocType {0} does not exist").format(self.query_doctype))

		condition = self.query_condition or "COUNT_EQUALS_ZERO"
		if condition not in CONDITIONS:
			frappe.throw(_("Unknown pass condition: {0}").format(condition))

		meta = frappe.get_meta(self.query_doctype)
		known = {df.fieldname: df for df in meta.fields}
		standard = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}

		def check(fieldname, label):
			if not fieldname or fieldname in standard:
				return
			df = known.get(fieldname)
			if not df:
				frappe.throw(
					_("{0}: field '{1}' does not exist on {2}").format(
						label, fieldname, self.query_doctype
					)
				)
			if df.fieldtype in FORBIDDEN_FIELDTYPES or fieldname in FORBIDDEN_FIELDNAMES:
				frappe.throw(
					_("{0}: field '{1}' holds a credential and cannot be recorded as evidence").format(
						label, fieldname
					)
				)

		if self.query_filters and self.query_filters.strip():
			try:
				filters = json.loads(self.query_filters)
			except ValueError as exc:
				frappe.throw(_("Filters are not valid JSON: {0}").format(exc))
			if not isinstance(filters, dict):
				frappe.throw(_("Filters must be a JSON object"))
			for key in filters:
				check(key, _("Filters"))

		if condition in ("ALL_ROWS_FIELD_SET", "ALL_ROWS_FIELD_EQUALS", "NO_ROW_OLDER_THAN_DAYS"):
			if not self.field_to_check:
				frappe.throw(_("Condition {0} needs a field to check").format(condition))
			check(self.field_to_check, _("Field to Check"))

		if condition == "ALL_ROWS_FIELD_EQUALS" and not (self.expected_value or "").strip():
			frappe.throw(_("Condition ALL_ROWS_FIELD_EQUALS needs an expected value"))
		if condition == "NO_ROW_OLDER_THAN_DAYS" and not cint(self.tolerance_days):
			frappe.throw(_("Condition NO_ROW_OLDER_THAN_DAYS needs a tolerance in days"))
		if condition in ("COUNT_LTE_THRESHOLD", "COUNT_GTE_THRESHOLD") and self.threshold_value is None:
			frappe.throw(_("Condition {0} needs a threshold").format(condition))

		# The handler field is unused in query mode; clearing it stops a stale
		# script path from being resurrected if the mode is switched back.
		self.handler = ""

	def validate_rules(self):
		for row in self.validation_rules:
			row.idx_label = f"Rule {row.idx}"
			if row.rule_type == "Regex Capture":
				try:
					re.compile(row.pattern or "")
				except re.error as exc:
					frappe.throw(_("Row {0}: invalid regex - {1}").format(row.idx, exc))
			if row.logic in (
				"GROUP1_EQUALS_EXPECTED", "GROUP1_GTE_EXPECTED", "GROUP1_LTE_EXPECTED",
				"VALUE_EQUALS_EXPECTED", "VALUE_GTE_EXPECTED", "VALUE_LTE_EXPECTED",
			) and row.expected_value in (None, ""):
				frappe.throw(_("Row {0}: this logic needs an expected value").format(row.idx))

		if self.emit_findings and self.controls and not self.validation_rules:
			frappe.msgprint(
				_("This fetcher emits findings but has no validation rule, so every "
				  "evaluation will be recorded as inconclusive."),
				indicator="orange",
				alert=True,
			)

	def resolve_controls(self):
		for row in self.controls:
			row.control = crosswalk.resolve_control(row.control_framework, row.control_id)
