"""Offline logic tests. Run with `python3 tests/test_offline_logic.py` - no bench required.

Exercises the pure-logic modules with a minimal Frappe stub.

contract.py and validators.py hold the rules that decide whether a compliance
record is auditable, so they are worth testing outside a bench.
"""

import sys, types, datetime, pathlib

# ------------------------------------------------------------------ frappe stub
frappe = types.ModuleType("frappe")


class ValidationError(Exception):
	pass


def _(msg):
	return msg


class _Utils(types.ModuleType):
	@staticmethod
	def now():
		return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


utils = _Utils("frappe.utils")
utils.now = _Utils.now


def _getdate(value):
	if isinstance(value, datetime.date):
		return value
	return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _nowdate():
	return datetime.date.today().isoformat()


def _add_days(date, days):
	return (_getdate(date) + datetime.timedelta(days=days)).isoformat()


utils.getdate = _getdate
utils.nowdate = _nowdate
utils.add_days = _add_days
utils.cint = lambda v: int(v or 0)
utils.flt = lambda v, *a: float(v or 0)

frappe.ValidationError = ValidationError
frappe._ = _
frappe.utils = utils


def throw(msg, exc=ValidationError, title=None):
	raise exc(msg)


frappe.throw = throw
sys.modules["frappe"] = frappe
sys.modules["frappe.utils"] = utils
frappe.get_meta = lambda dt: None
frappe.has_permission = lambda *a, **k: True
frappe.scrub = lambda s: s.lower().replace(" ", "_")
frappe.db = types.SimpleNamespace(exists=lambda *a, **k: True)
frappe.get_all = lambda *a, **k: []
frappe.PermissionError = type("PermissionError", (Exception,), {})

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from neogrc import contract  # noqa: E402
from neogrc.engine import validators  # noqa: E402

passed, failed = 0, 0


def check(label, condition, detail=""):
	global passed, failed
	if condition:
		passed += 1
		print(f"  ok   {label}")
	else:
		failed += 1
		print(f"  FAIL {label} {detail}")


# ---------------------------------------------------------------- contract tests
print("\ncontract.validate_finding")

good = {
	"schema_version": "1.0.0",
	"source": "aws-inspector",
	"source_version": "2026.04.01",
	"run_id": "01HXKJ",
	"collected_at": "2026-04-13T15:04:05Z",
	"resource": {"type": "aws_s3_bucket", "id": "acme-prod-logs",
	             "arn": "arn:aws:s3:::acme-prod-logs", "region": "us-east-1"},
	"evaluations": [{
		"control_framework": "NCC", "control_id": "CRY-01", "status": "fail",
		"severity": "high", "message": "Bucket has no default encryption",
		"remediation": {"summary": "Enable SSE-KMS", "effort_hours": 1,
		                "automation": "auto_fixable"},
	}],
}
check("valid finding passes", contract.validate_finding(good) == [],
      contract.validate_finding(good))

no_msg = {**good, "evaluations": [{**good["evaluations"][0], "message": ""}]}
errs = contract.validate_finding(no_msg)
check("fail without message rejected", any("message" in e for e in errs), errs)

no_sev = {**good, "evaluations": [{k: v for k, v in good["evaluations"][0].items()
                                    if k != "severity"}]}
errs = contract.validate_finding(no_sev)
check("fail without severity rejected", any("severity" in e for e in errs), errs)

inconclusive_ok = {**good, "evaluations": [{
	"control_framework": "NCC", "control_id": "CRY-01",
	"status": "inconclusive", "message": "AccessDenied on GetBucketEncryption"}]}
check("inconclusive with message passes",
      contract.validate_finding(inconclusive_ok) == [],
      contract.validate_finding(inconclusive_ok))

inconclusive_bad = {**good, "evaluations": [{
	"control_framework": "NCC", "control_id": "CRY-01", "status": "inconclusive"}]}
check("inconclusive without message rejected",
      contract.validate_finding(inconclusive_bad) != [])

check("pass needs no message",
      contract.validate_finding({**good, "evaluations": [{
	      "control_framework": "NCC", "control_id": "CRY-01", "status": "pass",
	      "severity": "info"}]}) == [])

check("bad status rejected",
      contract.validate_finding({**good, "evaluations": [{
	      "control_framework": "NCC", "control_id": "CRY-01", "status": "green"}]}) != [])

check("empty evaluations rejected",
      contract.validate_finding({**good, "evaluations": []}) != [])

check("missing resource rejected",
      contract.validate_finding({k: v for k, v in good.items() if k != "resource"}) != [])

check("schema major version enforced",
      any("schema_version" in e for e in
          contract.validate_finding({**good, "schema_version": "2.0.0"})))

check("negative effort rejected",
      contract.validate_finding({**good, "evaluations": [{
	      **good["evaluations"][0],
	      "remediation": {"effort_hours": -3}}]}) != [])

check("bad timestamp rejected",
      contract.validate_finding({**good, "collected_at": "13/04/2026"}) != [])

check("batch reports index",
      "findings[1]" in " ".join(contract.validate_batch([good, {**good, "source": ""}])))

print("\ncontract rollups")
evals = [{"status": "pass", "severity": "info"}, {"status": "fail", "severity": "medium"}]
check("fail wins rollup", contract.rollup_status(evals) == "fail")
check("worst severity of failures",
      contract.worst_severity([{"status": "fail", "severity": "medium"},
                               {"status": "pass", "severity": "critical"}]) == "medium",
      "a passing critical must not inflate the finding's severity")
check("inconclusive beats pass",
      contract.rollup_status([{"status": "pass", "severity": ""},
                              {"status": "inconclusive", "severity": ""}]) == "inconclusive")

print("\ncontract.normalise_timestamp")
check("Z suffix -> naive UTC",
      contract.normalise_timestamp("2026-04-13T15:04:05Z") == "2026-04-13 15:04:05")
check("offset converted to UTC",
      contract.normalise_timestamp("2026-04-13T18:04:05+03:00") == "2026-04-13 15:04:05")


# -------------------------------------------------------------- validator tests
print("\nvalidators.evaluate_all")


class Rule:
	def __init__(self, rule_type, pattern, logic, expected=None, sev="high", msg="failed"):
		self.rule_type, self.pattern, self.logic = rule_type, pattern, logic
		self.expected_value, self.severity_on_fail, self.message_on_fail = expected, sev, msg


aws_output = '{"summary": {"total_storage": 12, "encrypted_storage": 12}}'
r = validators.evaluate_all(
	[Rule("Regex Capture",
	      r'"total_storage":\s*(\d+),\s*"encrypted_storage":\s*(\d+)',
	      "GROUP1_EQUALS_GROUP2")],
	aws_output)
check("all volumes encrypted -> pass", r["status"] == "pass", r)

r = validators.evaluate_all(
	[Rule("Regex Capture",
	      r'"total_storage":\s*(\d+),\s*"encrypted_storage":\s*(\d+)',
	      "GROUP1_EQUALS_GROUP2", sev="critical")],
	'{"summary": {"total_storage": 12, "encrypted_storage": 9}}')
check("partial encryption -> fail", r["status"] == "fail", r)
check("failure carries severity", r["severity"] == "critical", r)

r = validators.evaluate_all([Rule("JSON Path", "users_without_2fa", "VALUE_LTE_EXPECTED", "0")],
                            '{"users_without_2fa": 0}')
check("json path 0 <= 0 -> pass", r["status"] == "pass", r)

r = validators.evaluate_all([Rule("JSON Path", "users_without_2fa", "VALUE_LTE_EXPECTED", "0")],
                            '{"users_without_2fa": 3}')
check("json path 3 <= 0 -> fail", r["status"] == "fail", r)

r = validators.evaluate_all([Rule("JSON Path", "summary.failed", "VALUE_LTE_EXPECTED", "0")],
                            '{"summary": {"failed": 0, "passed": 40}}')
check("nested json path resolves", r["status"] == "pass", r)

r = validators.evaluate_all([Rule("JSON Path", "missing.key", "VALUE_LTE_EXPECTED", "0")],
                            '{"summary": {}}')
check("absent json path -> inconclusive", r["status"] == "inconclusive", r)

r = validators.evaluate_all([Rule("JSON Path", "x", "VALUE_EQUALS_EXPECTED", "1")], "not json")
check("unparseable json -> inconclusive", r["status"] == "inconclusive", r)

r = validators.evaluate_all([], '{"anything": 1}')
check("no rules -> inconclusive, never pass", r["status"] == "inconclusive", r)

r = validators.evaluate_all(
	[Rule("Regex Capture", r'"detector_enabled":\s*(true|false)',
	      "GROUP1_EQUALS_EXPECTED", "true")],
	'{"detector_enabled": true}')
check("boolean group equals expected", r["status"] == "pass", r)

r = validators.evaluate_all([Rule("Regex Capture", r"AKIA[0-9A-Z]{16}", "MATCH_ABSENT",
                                  sev="critical")],
                            "no keys in this output")
check("secret absent -> pass", r["status"] == "pass", r)

r = validators.evaluate_all([Rule("Regex Capture", r"AKIA[0-9A-Z]{16}", "MATCH_ABSENT",
                                  sev="critical")],
                            "found AKIAIOSFODNN7EXAMPLE in config")
check("secret present -> fail", r["status"] == "fail", r)

r = validators.evaluate_all([Rule("Regex Capture", "([", "MATCH_EXISTS")], "x")
check("invalid regex -> inconclusive not crash", r["status"] == "inconclusive", r)

r = validators.evaluate_all([Rule("Non-Empty Output", "", "MATCH_EXISTS")], "")
check("empty output -> fail", r["status"] == "fail", r)

r = validators.evaluate_all([
	Rule("JSON Path", "a", "VALUE_LTE_EXPECTED", "0", sev="medium"),
	Rule("JSON Path", "b", "VALUE_LTE_EXPECTED", "0", sev="critical"),
], '{"a": 5, "b": 5}')
check("worst severity wins across rules", r["severity"] == "critical", r)

r = validators.evaluate_all([
	Rule("JSON Path", "a", "VALUE_LTE_EXPECTED", "0"),
	Rule("JSON Path", "missing", "VALUE_LTE_EXPECTED", "0"),
], '{"a": 5}')
check("fail outranks inconclusive", r["status"] == "fail", r)




# ------------------------------------------------------- query condition tests
print("\nquery_fetcher._evaluate")

from neogrc.engine import query_fetcher as qf  # noqa: E402

R = lambda **kw: dict(kw)

rows = [R(name="A", last="2026-01-01"), R(name="B", last="2026-08-01")]

failing, ok = qf._evaluate("COUNT_EQUALS_ZERO", [], "", 0, 0, "")
check("no rows -> pass", ok and not failing)

failing, ok = qf._evaluate("COUNT_EQUALS_ZERO", rows, "", 0, 0, "")
check("rows present -> fail, all rows reported", not ok and len(failing) == 2)

failing, ok = qf._evaluate("COUNT_LTE_THRESHOLD", rows, "", 5, 0, "")
check("2 <= 5 -> pass", ok and not failing)

failing, ok = qf._evaluate("COUNT_LTE_THRESHOLD", rows, "", 1, 0, "")
check("2 <= 1 -> fail", not ok and len(failing) == 2)

failing, ok = qf._evaluate("COUNT_GTE_THRESHOLD", rows, "", 1, 0, "")
check("2 >= 1 -> pass", ok)

failing, ok = qf._evaluate("COUNT_GTE_THRESHOLD", [], "", 1, 0, "")
check("absence fails GTE with no row to blame", not ok and failing == [])

failing, ok = qf._evaluate(
	"ALL_ROWS_FIELD_SET", [R(name="A", f="x"), R(name="B", f="")], "f", 0, 0, "")
check("empty field -> that row fails", not ok and len(failing) == 1)

failing, ok = qf._evaluate(
	"ALL_ROWS_FIELD_SET", [R(name="A", f=0)], "f", 0, 0, "")
check("zero counts as unset", not ok)

failing, ok = qf._evaluate(
	"ALL_ROWS_FIELD_EQUALS", [R(name="A", f=1), R(name="B", f=0)], "f", 0, 0, "1")
check("field equals expected -> only mismatches fail", not ok and len(failing) == 1)

failing, ok = qf._evaluate(
	"ALL_ROWS_FIELD_EQUALS", [], "f", 0, 0, "1")
check("no rows vacuously passes EQUALS", ok)

failing, ok = qf._evaluate("NO_ROW_OLDER_THAN_DAYS", rows, "last", 0, 3650, "")
check("everything inside tolerance -> pass", ok)

failing, ok = qf._evaluate("NO_ROW_OLDER_THAN_DAYS", rows, "last", 0, 1, "")
check("stale rows -> fail", not ok and len(failing) == 2)

failing, ok = qf._evaluate(
	"NO_ROW_OLDER_THAN_DAYS", [R(name="A", last=None)], "last", 0, 30, "")
check("missing date counts as stale, not as pass", not ok and len(failing) == 1)

failing, ok = qf._evaluate(
	"NO_ROW_OLDER_THAN_DAYS", [R(name="A", last="not-a-date")], "last", 0, 30, "")
check("unparseable date fails closed", not ok and len(failing) == 1)

print("\nquery_fetcher guards")
check("credential fieldnames are blocked",
      {"password", "api_secret", "token"} <= qf.FORBIDDEN_FIELDNAMES)
check("Password fieldtype is blocked", "Password" in qf.FORBIDDEN_FIELDTYPES)
check("condition list is closed", len(qf.CONDITIONS) == 6)


# ------------------------------------------------------------- demo fixtures
print("\ndemo fixtures")

import json as _json  # noqa: E402

FIX = pathlib.Path(__file__).resolve().parents[1] / "neogrc" / "demo" / "fixtures"

valid = _json.loads((FIX / "valid_findings.json").read_text())["findings"]
check("valid fixture passes the contract",
      contract.validate_batch(valid) == [], contract.validate_batch(valid)[:2])
check("valid fixture has a mix of statuses",
      {e["status"] for f in valid for e in f["evaluations"]} >=
      {"pass", "fail", "inconclusive"})

stale = _json.loads((FIX / "stale_findings.json").read_text())["findings"]
check("stale fixture passes the contract", contract.validate_batch(stale) == [])

invalid = _json.loads((FIX / "invalid_findings.json").read_text())["findings"]
reasons = ["message", "severity", "message", "status", "evaluations",
           "schema_version", "effort_hours", "collected_at"]
for i, (doc, reason) in enumerate(zip(invalid, reasons)):
	errs = contract.validate_finding(doc)
	check(f"invalid fixture[{i}] rejected for {reason}",
	      bool(errs) and any(reason in e for e in errs), errs)

check("whole invalid batch is rejected", contract.validate_batch(invalid) != [])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
