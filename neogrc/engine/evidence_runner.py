# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Evidence collection engine.

Executes the fetchers in an Evidence Set, stores every artifact with a SHA-256
hash, applies the validation rules, and converts the outcome into Findings that
satisfy the data contract.

Design notes worth keeping in mind when extending this:

* Shell fetchers are executed with ``shell=False`` and a resolved, sandboxed
  path. The connector declares a ``script_root`` and the fetcher handler must
  resolve inside it. This is what stops a Fetcher record - which a NeoGRC Engineer
  can create - from becoming a remote-code-execution primitive for anyone with
  desk access.
* Preflight checks (binaries present, env vars set) run before the first
  fetcher, so a missing ``aws`` CLI produces exit code 5 and a clear message
  rather than 30 opaque failures.
* Runs are always background jobs. A full AWS evidence set takes minutes and
  must not hold a web worker.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now, time_diff_in_seconds

from ..contract import (
	EXIT_AUTH,
	EXIT_GENERIC,
	EXIT_NOT_INSTALLED,
	EXIT_OK,
	EXIT_PARTIAL,
	EXIT_STATUS,
	SEVERITY_RANK,
	normalise_timestamp,
)
from . import validators

MAX_CAPTURE_BYTES = 32 * 1024 * 1024  # 32 MB per artifact
LOG_LIMIT = 60_000


def new_run_id() -> str:
	return uuid.uuid4().hex


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def enqueue_evidence_set(evidence_set: str, trigger_source: str = "Manual") -> str:
	"""Create a queued Evidence Run and hand it to the background worker."""
	doc = frappe.get_doc("NeoGRC Evidence Set", evidence_set)
	if not doc.enabled:
		frappe.throw(_("Evidence Set {0} is disabled").format(evidence_set))
	if not doc.fetchers:
		frappe.throw(_("Evidence Set {0} has no fetchers").format(evidence_set))

	run = frappe.new_doc("NeoGRC Evidence Run")
	run.run_id = new_run_id()
	run.evidence_set = evidence_set
	run.status = "Queued"
	run.triggered_by = frappe.session.user
	run.trigger_source = trigger_source
	run.fetchers_total = len(doc.fetchers)
	run.insert(ignore_permissions=True)
	frappe.db.commit()

	frappe.enqueue(
		"neogrc.engine.evidence_runner.execute_run",
		queue="long",
		timeout=3600,
		job_name=f"grc-evidence-{run.name}",
		enqueue_after_commit=True,
		run_name=run.name,
	)
	return run.name


def execute_run(run_name: str):
	"""Worker entry point. Never raises; failures are recorded on the run."""
	run = frappe.get_doc("NeoGRC Evidence Run", run_name)
	log: list = []
	try:
		_execute(run, log)
	except Exception:
		run.db_set("status", "Failed", update_modified=False)
		run.db_set("exit_code", EXIT_GENERIC, update_modified=False)
		log.append("FATAL: " + frappe.get_traceback())
		frappe.log_error(
			title=f"NeoGRC Evidence Run {run_name} failed",
			message=frappe.get_traceback(),
		)
	finally:
		run.db_set("run_log", "\n".join(log)[-LOG_LIMIT:], update_modified=False)
		run.db_set("finished_at", now(), update_modified=False)
		if run.started_at:
			run.db_set(
				"duration_seconds",
				time_diff_in_seconds(now(), run.started_at),
				update_modified=False,
			)
		frappe.db.commit()


# --------------------------------------------------------------------------- #
# Core loop
# --------------------------------------------------------------------------- #
def _execute(run, log: list):
	evidence_set = frappe.get_doc("NeoGRC Evidence Set", run.evidence_set)
	settings = frappe.get_cached_doc("NeoGRC Settings")

	if not settings.enabled:
		run.db_set("status", "Cancelled", update_modified=False)
		log.append("Evidence automation is disabled in NeoGRC Settings.")
		return

	run.db_set("status", "Running", update_modified=False)
	run.db_set("started_at", now(), update_modified=False)
	frappe.db.commit()

	base = _prepare_run_dir(settings, run)
	run.db_set("evidence_path", base, update_modified=False)
	log.append(f"Run {run.run_id} started at {now()} -> {base}")

	counts = {"ok": 0, "failed": 0, "pass": 0, "fail": 0, "inconclusive": 0}
	worst_exit = EXIT_OK
	connectors_seen = set()
	preflight_cache: dict = {}

	for item in evidence_set.fetchers:
		fetcher = frappe.get_doc("NeoGRC Evidence Fetcher", item.fetcher)
		if not fetcher.enabled:
			log.append(f"[skip] {fetcher.name} is disabled")
			continue

		connector = frappe.get_cached_doc("NeoGRC Connector", fetcher.connector)
		connectors_seen.add(connector.name)

		if not connector.enabled:
			log.append(f"[skip] connector {connector.name} is disabled")
			continue

		if connector.name not in preflight_cache:
			preflight_cache[connector.name] = _preflight(connector)
		ready, reason, code = preflight_cache[connector.name]

		if not ready:
			log.append(f"[{code}] {fetcher.name}: {reason}")
			worst_exit = max(worst_exit, code)
			counts["failed"] += 1
			_record_artifact(run, fetcher, base, "", code, {
				"status": "inconclusive", "message": reason, "severity": "", "results": [],
			}, reason)
			counts["inconclusive"] += _emit_findings(run, fetcher, connector, {
				"status": "inconclusive", "message": reason, "severity": "",
			}, [], base)
			continue

		result = _run_fetcher(connector, fetcher, base, settings)
		log.append(
			f"[{EXIT_STATUS.get(result['exit_code'], 'Failed')}] {fetcher.name} "
			f"({result['duration']:.1f}s, {len(result['stdout'])} bytes)"
		)

		validation = validators.evaluate_all(fetcher.validation_rules, result["stdout"])

		_record_artifact(
			run, fetcher, base, result["stdout"], result["exit_code"],
			validation, result["stderr"],
		)

		if result["exit_code"] == EXIT_OK:
			counts["ok"] += 1
		else:
			counts["failed"] += 1
			worst_exit = max(worst_exit, result["exit_code"])

		if fetcher.emit_findings:
			emitted = _emit_findings(
				run, fetcher, connector, validation, result.get("resources") or [], base
			)
			counts[validation["status"] if validation["status"] in counts else "inconclusive"] += emitted

		fetcher.db_set("last_run", now(), update_modified=False)
		fetcher.db_set(
			"last_status", EXIT_STATUS.get(result["exit_code"], "Failed"), update_modified=False
		)
		frappe.db.commit()

	if counts["failed"] and counts["ok"]:
		worst_exit = max(worst_exit, EXIT_PARTIAL)

	status = "Success"
	if counts["failed"] and not counts["ok"]:
		status = "Failed"
	elif counts["failed"]:
		status = "Partial"

	run.db_set({
		"status": status,
		"exit_code": worst_exit,
		"fetchers_succeeded": counts["ok"],
		"fetchers_failed": counts["failed"],
		"evaluations_pass": counts["pass"],
		"evaluations_fail": counts["fail"],
		"evaluations_inconclusive": counts["inconclusive"],
	}, update_modified=False)

	for connector_name in connectors_seen:
		_update_connector_state(connector_name, run, status)

	evidence_set.db_set("last_run", now(), update_modified=False)
	log.append(f"Run finished: {status} (exit {worst_exit}); {counts}")

	if status in ("Failed", "Partial") and evidence_set.notify_on_failure:
		_notify_failure(evidence_set, run, status)


# --------------------------------------------------------------------------- #
# Execution helpers
# --------------------------------------------------------------------------- #
def _prepare_run_dir(settings, run) -> str:
	base = settings.evidence_base_path or os.path.abspath(
		frappe.get_site_path("private", "files", "neogrc-evidence")
	)
	stamp = get_datetime(run.creation or now()).strftime("%Y%m%d-%H%M%S")
	path = os.path.join(base, frappe.local.site, f"{stamp}-{run.run_id[:8]}")
	os.makedirs(path, mode=0o750, exist_ok=True)
	return path


def _preflight(connector) -> tuple:
	"""Return ``(ready, reason, exit_code)`` for a connector."""
	for binary in _lines(connector.required_binaries):
		if not shutil.which(binary):
			return (
				False,
				_("Required binary '{0}' is not on PATH for connector {1}").format(
					binary, connector.name
				),
				EXIT_NOT_INSTALLED,
			)

	for var in _lines(connector.required_env_vars):
		if not os.environ.get(var):
			return (
				False,
				_("Environment variable '{0}' is not set; connector {1} cannot authenticate").format(
					var, connector.name
				),
				EXIT_AUTH,
			)

	if connector.handler_type == "Shell":
		if not connector.script_root:
			return (False, _("Connector has no script root configured"), EXIT_NOT_INSTALLED)
		if not os.path.isdir(connector.script_root):
			return (
				False,
				_("Script root {0} does not exist on this server").format(connector.script_root),
				EXIT_NOT_INSTALLED,
			)

	return (True, "", EXIT_OK)


def _lines(value) -> list:
	return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _resolve_script(connector, handler: str) -> str:
	"""Resolve a shell handler inside the connector's script root.

	Rejects anything that escapes the root, including symlinks pointing out of
	it. A fetcher record is operator-supplied data, not trusted code.
	"""
	root = os.path.realpath(connector.script_root)
	target = os.path.realpath(os.path.join(root, handler))
	if not (target == root or target.startswith(root + os.sep)):
		frappe.throw(
			_("Fetcher handler {0} resolves outside the connector script root").format(handler),
			frappe.PermissionError,
		)
	if not os.path.isfile(target):
		raise FileNotFoundError(target)
	if not os.access(target, os.X_OK):
		raise PermissionError(f"{target} is not executable")
	return target


def _run_fetcher(connector, fetcher, base: str, settings) -> dict:
	timeout = cint(fetcher.timeout) or cint(connector.timeout) or cint(settings.default_run_timeout) or 300
	started = now()

	# Query mode is a property of the fetcher, not the connector, so a single
	# internal connector can carry both coded handlers and declarative queries.
	if fetcher.get("handler_mode") == "Query" or connector.handler_type == "Internal Query":
		return _run_query(connector, fetcher, started)
	if connector.handler_type == "Python":
		return _run_python(connector, fetcher, timeout, started)
	return _run_shell(connector, fetcher, base, timeout, started)


def _run_query(connector, fetcher, started: str) -> dict:
	"""Run a declarative DocType query. No subprocess, no dotted import."""
	from .query_fetcher import run_query

	try:
		payload, resources = run_query(connector=connector, fetcher=fetcher)
	except frappe.PermissionError as exc:
		return _result("", str(exc), EXIT_AUTH, started)
	except frappe.ValidationError as exc:
		# A malformed query is a configuration fault, not a collection failure,
		# so it reports as not-installed rather than as a failing control.
		return _result("", str(exc), EXIT_NOT_INSTALLED, started)
	except Exception:
		return _result("", frappe.get_traceback(), EXIT_GENERIC, started)

	out = _result(json.dumps(payload, indent=2, default=str), "", EXIT_OK, started)
	out["resources"] = resources
	return out


def _run_python(connector, fetcher, timeout: int, started: str) -> dict:
	"""Call a dotted Python handler from an installed app.

	The handler receives the connector and fetcher documents and returns either
	a JSON-serialisable object or a ``(payload, resources)`` tuple, where
	``resources`` is a list of resource dicts so one fetcher can emit a finding
	per resource instead of a single aggregate.
	"""
	try:
		method = frappe.get_attr(fetcher.handler)
	except Exception as exc:
		return _result("", str(exc), EXIT_NOT_INSTALLED, started)

	try:
		payload = method(connector=connector, fetcher=fetcher)
		resources = []
		if isinstance(payload, tuple) and len(payload) == 2:
			payload, resources = payload
		text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, default=str)
		out = _result(text, "", EXIT_OK, started)
		out["resources"] = resources
		return out
	except frappe.AuthenticationError as exc:
		return _result("", str(exc), EXIT_AUTH, started)
	except Exception:
		return _result("", frappe.get_traceback(), EXIT_GENERIC, started)


def _run_shell(connector, fetcher, base: str, timeout: int, started: str) -> dict:
	try:
		script = _resolve_script(connector, fetcher.handler)
	except FileNotFoundError as exc:
		return _result("", f"Script not found: {exc}", EXIT_NOT_INSTALLED, started)
	except PermissionError as exc:
		return _result("", str(exc), EXIT_NOT_INSTALLED, started)

	argv = [script] + _lines(fetcher.arguments)
	env = os.environ.copy()
	env.update({
		"GRC_EVIDENCE_DIR": base,
		"GRC_FETCHER_ID": fetcher.name,
		"GRC_CONNECTOR_ID": connector.name,
	})

	try:
		proc = subprocess.run(  # noqa: S603 - argv is a resolved, sandboxed path
			argv,
			capture_output=True,
			text=True,
			timeout=timeout,
			cwd=connector.script_root,
			env=env,
			shell=False,
			check=False,
		)
	except subprocess.TimeoutExpired:
		return _result("", f"Timed out after {timeout}s", EXIT_GENERIC, started)
	except OSError as exc:
		return _result("", str(exc), EXIT_NOT_INSTALLED, started)

	code = proc.returncode
	if code not in EXIT_STATUS:
		code = EXIT_OK if code == 0 else EXIT_GENERIC

	return _result(
		(proc.stdout or "")[:MAX_CAPTURE_BYTES],
		(proc.stderr or "")[:8000],
		code,
		started,
	)


def _result(stdout: str, stderr: str, exit_code: int, started: str) -> dict:
	return {
		"stdout": stdout,
		"stderr": stderr,
		"exit_code": exit_code,
		"duration": time_diff_in_seconds(now(), started),
		"resources": [],
	}


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _record_artifact(run, fetcher, base: str, output: str, exit_code: int,
                     validation: dict, stderr: str) -> str:
	settings = frappe.get_cached_doc("NeoGRC Settings")
	safe = frappe.scrub(fetcher.name)
	filename = f"{safe}.json" if fetcher.output_format == "JSON" else f"{safe}.txt"
	path = os.path.join(base, filename)

	data = (output or "").encode("utf-8")
	with open(path, "wb") as fh:
		fh.write(data)
	os.chmod(path, 0o640)

	artifact = frappe.new_doc("NeoGRC Evidence Artifact")
	artifact.evidence_run = run.name
	artifact.fetcher = fetcher.name
	artifact.collected_at = now()
	artifact.file_name = filename
	artifact.file_path = path
	artifact.content_type = "application/json" if fetcher.output_format == "JSON" else "text/plain"
	artifact.size_bytes = len(data)
	artifact.exit_code = exit_code
	artifact.validation_status = validation.get("status")
	artifact.validation_detail = json.dumps(validation.get("results") or [], indent=1, default=str)
	artifact.stderr_excerpt = (stderr or "")[:1000]

	if settings.hash_artifacts:
		artifact.sha256 = hashlib.sha256(data).hexdigest()

	artifact.insert(ignore_permissions=True)

	if settings.attach_artifacts_to_run and data:
		try:
			file_doc = frappe.get_doc({
				"doctype": "File",
				"file_name": filename,
				"attached_to_doctype": "NeoGRC Evidence Artifact",
				"attached_to_name": artifact.name,
				"is_private": 1,
				"content": data,
			}).insert(ignore_permissions=True)
			artifact.db_set("artifact_file", file_doc.file_url, update_modified=False)
		except Exception:
			frappe.log_error(
				title="NeoGRC artifact attach failed", message=frappe.get_traceback()
			)

	return artifact.name


def _emit_findings(run, fetcher, connector, validation: dict, resources: list, base: str) -> int:
	"""Convert a validated fetcher result into contract-conformant Findings."""
	if not fetcher.controls:
		return 0

	status = validation.get("status", "inconclusive")
	message = validation.get("message") or ""
	collected = normalise_timestamp(now())

	targets = resources or [{
		"type": f"{connector.name}:evidence",
		"id": fetcher.name,
	}]

	created = 0
	for resource in targets:
		doc = frappe.new_doc("NeoGRC Finding")
		doc.source = connector.name
		doc.source_version = connector.source_version or "0.1.0"
		doc.run_id = run.run_id
		doc.evidence_run = run.name
		doc.collected_at = collected
		doc.resource_type = resource.get("type") or "evidence"
		doc.resource_id = str(resource.get("id") or fetcher.name)[:140]
		doc.resource_arn = resource.get("arn")
		doc.region = resource.get("region")
		doc.account_id = resource.get("account_id")
		if resource.get("tags"):
			doc.resource_tags = json.dumps(resource["tags"], default=str)
		if resource.get("raw"):
			doc.raw_attributes = json.dumps(resource["raw"], indent=1, default=str)[:100000]

		for link in fetcher.controls:
			severity = ""
			if status == "fail":
				severity = validation.get("severity") or link.severity or "medium"
			elif status in ("pass", "not_applicable"):
				severity = "info"

			doc.append("evaluations", {
				"control_framework": link.control_framework,
				"control_id": link.control_id,
				"control": link.control,
				"status": status,
				"severity": severity,
				"message": message[:1000] if status in ("fail", "inconclusive") else "",
				"evidence_refs": os.path.relpath(base, os.path.dirname(base)) + "/" + frappe.scrub(fetcher.name),
				"assessed_at": collected,
			})

		doc.insert(ignore_permissions=True)
		created += 1

	return created


def _update_connector_state(connector_name: str, run, status: str):
	failures = 0
	if status != "Success":
		failures = cint(frappe.db.get_value("NeoGRC Connector", connector_name, "consecutive_failures")) + 1

	frappe.db.set_value(
		"NeoGRC Connector",
		connector_name,
		{
			"last_run": now(),
			"last_status": status if status != "Cancelled" else "Failed",
			"last_run_id": run.run_id,
			"consecutive_failures": failures,
		},
		update_modified=False,
	)


def _notify_failure(evidence_set, run, status: str):
	recipient = evidence_set.owner_user or evidence_set.owner
	if not recipient:
		return
	try:
		frappe.sendmail(
			recipients=[recipient],
			subject=_("NeoGRC evidence run {0} finished with status {1}").format(run.name, status),
			message=_(
				"Evidence Set: {0}<br>Run: {1}<br>Status: {2}<br>"
				"Failed fetchers: {3} of {4}<br><br>Open the Evidence Run for the full log."
			).format(
				evidence_set.name, run.name, status,
				run.fetchers_failed, run.fetchers_total,
			),
			reference_doctype="NeoGRC Evidence Run",
			reference_name=run.name,
		)
	except Exception:
		frappe.log_error(title="NeoGRC failure notification", message=frappe.get_traceback())
