# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Evidence validation rules.

Modelled on the ``validationRules`` block in the Paramify evidence-fetchers
catalog, where a rule is a regex plus a comparison such as
``IF match.group(1) == match.group(2) THEN PASS``. That repo stores the logic as
a free-text string and evaluates it in Python. Executing operator-authored
strings inside a Frappe site is not acceptable, so the logic is an enumerated
Select and this module dispatches on it. The expressiveness lost is small; the
arbitrary-code-execution surface removed is not.

A JSON-path rule type is added because most fetchers already emit structured
JSON, and pulling ``summary.encrypted_volumes`` out of a dict is more robust
than pattern-matching its serialisation.
"""

from __future__ import annotations

import json
import re

MAX_OUTPUT_SCAN = 4 * 1024 * 1024  # 4 MB; larger artifacts are validated by header only


class RuleOutcome:
	__slots__ = ("status", "message", "severity", "detail")

	def __init__(self, status: str, message: str = "", severity: str = "", detail=None):
		self.status = status
		self.message = message
		self.severity = severity
		self.detail = detail or {}

	def as_dict(self):
		return {
			"status": self.status,
			"message": self.message,
			"severity": self.severity,
			"detail": self.detail,
		}


def _num(value):
	try:
		return float(str(value).strip())
	except (TypeError, ValueError):
		return None


def _json_path(payload, path: str):
	"""Resolve a dotted path. Numeric segments index into lists."""
	current = payload
	for segment in str(path or "").split("."):
		segment = segment.strip()
		if segment == "":
			continue
		if isinstance(current, list):
			if not segment.isdigit() or int(segment) >= len(current):
				return None
			current = current[int(segment)]
		elif isinstance(current, dict):
			if segment not in current:
				return None
			current = current[segment]
		else:
			return None
	return current


def evaluate_rule(rule, output: str, parsed=None) -> RuleOutcome:
	"""Evaluate one NeoGRC Validation Rule row against fetcher output."""
	rule_type = rule.rule_type or "Regex Capture"
	logic = rule.logic or "MATCH_EXISTS"
	expected = rule.expected_value
	severity = rule.severity_on_fail or "high"
	fail_msg = rule.message_on_fail or f"Validation rule failed ({logic})"

	if rule_type == "Non-Empty Output":
		if (output or "").strip():
			return RuleOutcome("pass", detail={"bytes": len(output or "")})
		return RuleOutcome("fail", "Fetcher produced no output", severity)

	if rule_type in ("JSON Path", "JSON Key Equals"):
		if parsed is None:
			return RuleOutcome(
				"inconclusive",
				"Rule expects JSON but the fetcher output could not be parsed",
				severity,
			)
		value = _json_path(parsed, rule.pattern)
		return _apply_value_logic(value, logic, expected, severity, fail_msg)

	# Regex Capture
	try:
		pattern = re.compile(rule.pattern or "", re.MULTILINE | re.DOTALL)
	except re.error as exc:
		return RuleOutcome("inconclusive", f"Invalid regex: {exc}", severity)

	haystack = (output or "")[:MAX_OUTPUT_SCAN]
	match = pattern.search(haystack)

	if logic == "MATCH_ABSENT":
		if match:
			return RuleOutcome("fail", fail_msg, severity, {"matched": match.group(0)[:200]})
		return RuleOutcome("pass")

	if not match:
		if logic == "MATCH_EXISTS":
			return RuleOutcome("fail", fail_msg, severity)
		return RuleOutcome(
			"inconclusive",
			"Pattern did not match; the evidence could not be evaluated",
			severity,
		)

	if logic == "MATCH_EXISTS":
		return RuleOutcome("pass", detail={"matched": match.group(0)[:200]})

	groups = match.groups()
	detail = {"groups": [g for g in groups][:8]}

	if logic == "ALL_GROUPS_EQUAL":
		if groups and len({(g or "").strip() for g in groups}) == 1:
			return RuleOutcome("pass", detail=detail)
		return RuleOutcome("fail", fail_msg, severity, detail)

	if logic == "GROUP1_EQUALS_GROUP2":
		if len(groups) < 2:
			return RuleOutcome("inconclusive", "Regex captured fewer than two groups", severity, detail)
		if (groups[0] or "").strip() == (groups[1] or "").strip():
			return RuleOutcome("pass", detail=detail)
		return RuleOutcome("fail", fail_msg, severity, detail)

	if not groups:
		return RuleOutcome("inconclusive", "Regex captured no groups", severity, detail)

	return _apply_value_logic(groups[0], logic.replace("GROUP1_", "VALUE_"), expected, severity, fail_msg, detail)


def _apply_value_logic(value, logic, expected, severity, fail_msg, detail=None):
	detail = dict(detail or {})
	detail["value"] = value

	if value is None:
		return RuleOutcome("inconclusive", "Value not present in evidence", severity, detail)

	if logic in ("VALUE_EQUALS_EXPECTED", "MATCH_EXISTS"):
		if logic == "MATCH_EXISTS":
			return RuleOutcome("pass", detail=detail)
		if str(value).strip().lower() == str(expected or "").strip().lower():
			return RuleOutcome("pass", detail=detail)
		return RuleOutcome("fail", fail_msg, severity, detail)

	if logic in ("VALUE_GTE_EXPECTED", "VALUE_LTE_EXPECTED"):
		actual, target = _num(value), _num(expected)
		if actual is None or target is None:
			return RuleOutcome("inconclusive", "Non-numeric comparison", severity, detail)
		ok = actual >= target if logic == "VALUE_GTE_EXPECTED" else actual <= target
		return RuleOutcome("pass" if ok else "fail", "" if ok else fail_msg, severity, detail)

	if logic == "ALL_GROUPS_EQUAL":
		return RuleOutcome("pass", detail=detail)

	return RuleOutcome("inconclusive", f"Unsupported logic '{logic}'", severity, detail)


def evaluate_all(rules, output: str) -> dict:
	"""Run every rule for a fetcher. Worst outcome wins.

	Returns ``{"status", "message", "severity", "results"}``. With no rules the
	status is ``inconclusive`` rather than ``pass``: collecting a file is not
	the same as demonstrating a control, and silently passing unvalidated
	evidence is how automated compliance programmes drift.
	"""
	parsed = None
	if output:
		try:
			parsed = json.loads(output)
		except (ValueError, TypeError):
			parsed = None

	if not rules:
		return {
			"status": "inconclusive",
			"message": "Evidence collected but no validation rule is defined for this fetcher",
			"severity": "",
			"results": [],
		}

	results, failures, inconclusive = [], [], []
	for rule in rules:
		outcome = evaluate_rule(rule, output, parsed)
		row = outcome.as_dict()
		row["rule_type"] = rule.rule_type
		row["logic"] = rule.logic
		results.append(row)
		if outcome.status == "fail":
			failures.append(outcome)
		elif outcome.status == "inconclusive":
			inconclusive.append(outcome)

	if failures:
		from ..contract import SEVERITY_RANK

		worst = max(failures, key=lambda o: SEVERITY_RANK.get(o.severity, 0))
		return {
			"status": "fail",
			"message": "; ".join(o.message for o in failures if o.message)[:1000] or "Validation failed",
			"severity": worst.severity or "high",
			"results": results,
		}

	if inconclusive:
		return {
			"status": "inconclusive",
			"message": "; ".join(o.message for o in inconclusive if o.message)[:1000],
			"severity": "",
			"results": results,
		}

	return {"status": "pass", "message": "", "severity": "info", "results": results}
