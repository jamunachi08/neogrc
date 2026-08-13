#!/usr/bin/env python3
# Copyright (c) 2026, Neotec Integrated Solutions and contributors
"""Structural guard for neogrc.

Run before packaging or committing. It checks the things that break silently at
`bench migrate` time rather than at import time, plus the licensing rule that
matters commercially: no normative control text from a licensed standard may be
committed into the seed data.

    python3 verify_tree.py
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent
APP = ROOT / "neogrc"
MODULE = APP / "neogrc_engine"
DATA = APP / "setup" / "data"

errors: list[str] = []
warnings: list[str] = []


def err(msg):
	errors.append(msg)


def warn(msg):
	warnings.append(msg)


# --------------------------------------------------------------------------- #
def check_layout():
	required = [
		APP / "hooks.py", APP / "modules.txt", APP / "patches.txt",
		APP / "contract.py", APP / "crosswalk.py", APP / "api.py",
		APP / "engine" / "evidence_runner.py", APP / "engine" / "gap.py",
		APP / "engine" / "validators.py",
		APP / "setup" / "install.py", APP / "setup" / "scheduler.py",
		ROOT / "pyproject.toml", ROOT / "license.txt", ROOT / "CHANGELOG.md",
	]
	for path in required:
		if not path.exists():
			err(f"missing required file: {path.relative_to(ROOT)}")

	modules = (APP / "modules.txt").read_text().split()
	if modules != ["NeoGRC", "Engine"]:
		err(f"modules.txt should contain exactly 'NeoGRC Engine', found {modules}")

	for pkg in (APP, APP / "engine", APP / "setup", APP / "patches", MODULE,
	            MODULE / "doctype", MODULE / "report"):
		if pkg.is_dir() and not (pkg / "__init__.py").exists():
			err(f"package directory without __init__.py: {pkg.relative_to(ROOT)}")


def check_patches():
	text = (APP / "patches.txt").read_text()
	if "[post_model_sync]" not in text:
		err("patches.txt has no [post_model_sync] section")
		return

	post = text.split("[post_model_sync]", 1)[1]
	pre = text.split("[post_model_sync]", 1)[0]

	for line in [ln.strip() for ln in pre.splitlines() if ln.strip() and not ln.startswith("[")]:
		warn(f"patch in [pre_model_sync]: {line} - confirm it does not touch app doctypes")

	for line in [ln.strip() for ln in post.splitlines() if ln.strip()]:
		parts = line.split(".")
		path = APP.joinpath(*parts[1:]).with_suffix(".py")
		if not path.exists():
			err(f"patches.txt references a missing module: {line}")
		elif "def execute" not in path.read_text():
			err(f"patch {line} has no execute() function")


def check_doctypes():
	seen, links = {}, []
	for folder in sorted((MODULE / "doctype").iterdir()):
		if not folder.is_dir() or folder.name.startswith(("__", ".")):
			continue
		js = folder / f"{folder.name}.json"
		if not js.exists():
			err(f"doctype folder without json: {folder.name}")
			continue

		doc = json.loads(js.read_text())
		name = doc["name"]
		seen[name] = doc

		# Frappe derives the controller module path from scrub(doctype name), not
		# from the folder name. If they disagree, `bench install-app` dies partway
		# through DocType sync with "No module named ...", leaving a half-installed
		# site. Nothing else in this file catches that, because both the JSON and
		# the folder are individually well-formed.
		expected = name.lower().replace(" ", "_").replace("-", "_")
		if folder.name != expected:
			err(f"{name}: folder is '{folder.name}' but Frappe will import "
			    f"'{expected}' - rename the folder and its .py/.json/test_ files")
			continue

		for suffix in (".json", ".py"):
			if not (folder / f"{expected}{suffix}").exists():
				err(f"{name}: missing {expected}{suffix}")

		if doc.get("module") != "NeoGRC Engine":
			err(f"{name}: module is '{doc.get('module')}', expected 'NeoGRC Engine'")

		order = doc.get("field_order", [])
		fields = [f["fieldname"] for f in doc.get("fields", [])]
		if sorted(order) != sorted(fields):
			err(f"{name}: field_order does not match fields")

		if len(fields) != len(set(fields)):
			dupes = {f for f in fields if fields.count(f) > 1}
			err(f"{name}: duplicate fieldnames {dupes}")

		if not doc.get("istable") and not doc.get("permissions"):
			err(f"{name}: no permission rules")

		py = folder / f"{folder.name}.py"
		if not py.exists():
			err(f"{name}: missing controller module")
		else:
			source = py.read_text()
			try:
				tree = ast.parse(source)
			except SyntaxError as exc:
				err(f"{name}: controller syntax error - {exc}")
			else:
				classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
				if not doc.get("istable") and not classes:
					err(f"{name}: controller defines no class")

		for field in doc.get("fields", []):
			if field["fieldtype"] in ("Link", "Table", "Table MultiSelect"):
				if not field.get("options"):
					err(f"{name}.{field['fieldname']}: {field['fieldtype']} without options")
				else:
					links.append((name, field["fieldname"], field["fieldtype"], field["options"]))
			if field.get("fetch_from") and "." not in field["fetch_from"]:
				err(f"{name}.{field['fieldname']}: malformed fetch_from")

	# Every Table target must exist and be a child table; every Link target to a
	# GRC doctype must exist in this app.
	for parent, fieldname, fieldtype, target in links:
		if fieldtype == "Table":
			if target not in seen:
				err(f"{parent}.{fieldname}: table target '{target}' not found")
			elif not seen[target].get("istable"):
				err(f"{parent}.{fieldname}: '{target}' is not a child table")
		elif target.startswith("NeoGRC ") and target not in seen:
			err(f"{parent}.{fieldname}: link target '{target}' not found")

	return seen


def check_report_and_workspace_paths():
	"""Reports and workspaces are imported by folder name too.

	Same failure mode as the doctype check above: a folder that does not match
	scrub(name) breaks install partway through, after some records are already
	committed.
	"""
	def scrub(name):
		return name.lower().replace(" ", "_").replace("-", "_")

	modules = set((APP / "modules.txt").read_text().split("\n"))
	modules = {m.strip() for m in modules if m.strip()}

	for kind in ("report", "workspace"):
		root = MODULE / kind
		if not root.is_dir():
			continue
		for folder in sorted(root.iterdir()):
			if not folder.is_dir() or folder.name.startswith(("__", ".")):
				continue
			candidates = list(folder.glob("*.json"))
			if not candidates:
				err(f"{kind} folder without json: {folder.name}")
				continue
			doc = json.loads(candidates[0].read_text())
			expected = scrub(doc["name"])
			if folder.name != expected:
				err(f"{kind} '{doc['name']}': folder is '{folder.name}' but Frappe "
				    f"will look for '{expected}'")
			if doc.get("module") not in modules:
				err(f"{kind} '{doc['name']}': module '{doc.get('module')}' is not "
				    f"in modules.txt")
			if kind == "report":
				if doc.get("report_type") == "Script Report" and not (
					folder / f"{expected}.py"
				).exists():
					err(f"report '{doc['name']}': script report with no controller")
				ref = doc.get("ref_doctype")
				if ref and not (MODULE / "doctype" / scrub(ref)).exists():
					err(f"report '{doc['name']}': ref_doctype '{ref}' not found")


def check_doctype_modules(doctypes):
	"""Every doctype's declared module must appear in modules.txt."""
	modules = set((APP / "modules.txt").read_text().split("\n"))
	modules = {m.strip() for m in modules if m.strip()}
	for name, doc in doctypes.items():
		if doc.get("module") not in modules:
			err(f"{name}: module '{doc.get('module')}' is not in modules.txt")


def check_defaults(doctypes):
	"""Catch defaults that install-time validation will reject.

	A DocType default is written before any controller can normalise it, so a
	default that a validate() hook then refuses turns after_install into a hard
	failure with the tables already created. An absolute filesystem path is the
	usual offender: it is correct on the author's bench and unwritable on managed
	hosting.
	"""
	for name, doc in doctypes.items():
		for field in doc.get("fields", []):
			default = field.get("default")
			if default in (None, ""):
				continue
			fieldtype = field["fieldtype"]

			if fieldtype == "Select":
				options = (field.get("options") or "").split("\n")
				if default not in options:
					err(f"{name}.{field['fieldname']}: default {default!r} is not "
					    f"one of its Select options")

			if fieldtype == "Link":
				if doc.get("issingle"):
					# Frappe's post_install calls init_singles(), which saves every
					# Single before any app hook runs. A Link default there is
					# validated against a table that after_install has not populated
					# yet, so install fails at 100% of DocType sync with the app
					# already added to installed_apps.
					err(f"{name}.{field['fieldname']}: Link default {default!r} on a "
					    f"Single - init_singles() saves this during install, before "
					    f"after_install seeds anything. Set it in configure_settings "
					    f"instead")
				else:
					warn(f"{name}.{field['fieldname']}: Link default {default!r} - the "
					     f"target must already exist whenever a record is created")

			if doc.get("issingle") and field.get("reqd"):
				warn(f"{name}.{field['fieldname']}: mandatory field on a Single - "
				     f"init_singles() sets ignore_mandatory, but every later save "
				     f"will need a value")

			if fieldtype in ("Data", "Small Text", "Text") and str(default).startswith("/"):
				err(f"{name}.{field['fieldname']}: hardcoded filesystem path default "
				    f"{default!r} - resolve it in configure_settings instead, since "
				    f"this path will not exist or be writable on managed hosting")

			if fieldtype in ("Int", "Float", "Percent", "Check"):
				try:
					float(default)
				except (TypeError, ValueError):
					err(f"{name}.{field['fieldname']}: non-numeric default {default!r}")


def check_external_links(doctypes):
	"""Warn when a Link target lives outside this app and its declared deps.

	Frappe validates Link options at DocType insert. A target from an app that is
	not in required_apps means install dies partway through on a site that lacks
	it, with some tables already created.
	"""
	hooks = (APP / "hooks.py").read_text()
	match = re.search(r"required_apps\s*=\s*\[([^\]]*)\]", hooks)
	declared = set(re.findall(r"[\"\']([\w_]+)[\"\']", match.group(1))) if match else set()

	FRAPPE_CORE = {"User", "Role", "File", "DocType", "Contact", "Address",
	               "Currency", "Country", "Print Format", "Workflow"}
	ERPNEXT = {"Company", "Supplier", "Customer", "Employee", "Item", "Warehouse",
	           "Cost Center", "Department", "Designation", "Project", "Task",
	           "Fiscal Year", "Accounting Dimension"}

	for name, doc in doctypes.items():
		for field in doc.get("fields", []):
			if field["fieldtype"] not in ("Link", "Table", "Table MultiSelect"):
				continue
			target = field.get("options")
			if not target or target in doctypes or target in FRAPPE_CORE:
				continue
			if target in ERPNEXT:
				if "erpnext" not in declared:
					err(f"{name}.{field['fieldname']} links to ERPNext doctype "
					    f"'{target}' but erpnext is not in required_apps")
			else:
				warn(f"{name}.{field['fieldname']} links to '{target}', which is "
				     f"outside this app - confirm the owning app is a declared "
				     f"dependency")


def check_seed_data(doctypes):
	frameworks = json.loads((DATA / "frameworks.json").read_text())
	controls = json.loads((DATA / "controls.json").read_text())
	crosswalks = json.loads((DATA / "crosswalks.json").read_text())
	connectors = json.loads((DATA / "connectors.json").read_text())
	fetchers = json.loads((DATA / "fetchers.json").read_text())
	sets = json.loads((DATA / "evidence_sets.json").read_text())

	fw_codes = {f["framework_code"] for f in frameworks}
	canonical = [f for f in frameworks if f.get("is_canonical")]
	if len(canonical) != 1:
		err(f"exactly one framework must be canonical, found {len(canonical)}")

	control_keys = {(c["framework"], c["control_id"]) for c in controls}
	for c in controls:
		if c["framework"] not in fw_codes:
			err(f"control {c['control_id']} references unknown framework {c['framework']}")

	for x in crosswalks:
		if (x["source_framework"], x["source_control_id"]) not in control_keys:
			err(f"crosswalk source not in control seed: {x['source_control_id']}")
		if x["target_framework"] not in fw_codes:
			err(f"crosswalk target framework unknown: {x['target_framework']}")

	conn_ids = {c["connector_id"] for c in connectors}
	fetcher_ids = {f["fetcher_id"] for f in fetchers}

	CONDITIONS = {
		"COUNT_EQUALS_ZERO", "COUNT_LTE_THRESHOLD", "COUNT_GTE_THRESHOLD",
		"ALL_ROWS_FIELD_SET", "ALL_ROWS_FIELD_EQUALS", "NO_ROW_OLDER_THAN_DAYS",
	}
	our_doctypes = {d["name"] for d in doctypes.values()}

	for f in fetchers:
		if f.get("handler_mode") == "Query":
			if not f.get("query_doctype"):
				err(f"query fetcher {f['fetcher_id']} has no source doctype")
			elif f["query_doctype"].startswith("NeoGRC ") and f["query_doctype"] not in our_doctypes:
				err(f"query fetcher {f['fetcher_id']} targets a missing doctype "
				    f"{f['query_doctype']}")
			if f.get("query_condition") not in CONDITIONS:
				err(f"query fetcher {f['fetcher_id']}: unknown condition "
				    f"{f.get('query_condition')!r}")
			try:
				parsed = json.loads(f.get("query_filters") or "{}")
				if not isinstance(parsed, dict):
					err(f"query fetcher {f['fetcher_id']}: filters must be a JSON object")
			except ValueError as exc:
				err(f"query fetcher {f['fetcher_id']}: filters are not valid JSON - {exc}")
			if f.get("handler"):
				err(f"query fetcher {f['fetcher_id']} must not carry a script handler")
		elif not f.get("handler"):
			err(f"fetcher {f['fetcher_id']} has no handler")

		if f["connector"] not in conn_ids:
			err(f"fetcher {f['fetcher_id']} references unknown connector {f['connector']}")
		for link in f.get("controls", []):
			if (link["control_framework"], link["control_id"]) not in control_keys:
				err(f"fetcher {f['fetcher_id']} maps to unknown control "
				    f"{link['control_framework']}::{link['control_id']}")
		for rule in f.get("validation_rules", []):
			if rule["rule_type"] == "Regex Capture":
				try:
					re.compile(rule["pattern"])
				except re.error as exc:
					err(f"fetcher {f['fetcher_id']}: invalid regex - {exc}")

	for s in sets:
		if s.get("target_framework") and s["target_framework"] not in fw_codes:
			err(f"evidence set {s['set_id']} references unknown framework")
		for item in s.get("fetchers", []):
			if item["fetcher"] not in fetcher_ids:
				err(f"evidence set {s['set_id']} references unknown fetcher {item['fetcher']}")

	# Any shell connector shipping with a script_root pre-filled would point the
	# runner at a path the installer never verified.
	for c in connectors:
		if c.get("handler_type") == "Shell" and c.get("enabled"):
			err(f"connector {c['connector_id']}: shell connectors must ship disabled")
		if c.get("script_root"):
			err(f"connector {c['connector_id']}: script_root must not be seeded")

	return {"frameworks": len(frameworks), "controls": len(controls),
	        "crosswalks": len(crosswalks), "connectors": len(connectors),
	        "fetchers": len(fetchers), "sets": len(sets)}


# Phrases that would indicate normative standard text was pasted in. Seed
# objectives are original wording; these are the shapes copied clauses take.
FORBIDDEN = [
	r"\bthe organization shall\b",
	r"\bthe organisation shall\b",
	r"\bshall establish, implement, maintain\b",
	r"\bISO/IEC 27001:2022, Clause\b",
	r"\bReproduced with permission\b",
	r"\bCopyright .{0,40}(ISO|AICPA|PCI Security Standards)\b",
]


def check_no_licensed_text():
	for path in DATA.glob("*.json"):
		text = path.read_text()
		for pattern in FORBIDDEN:
			match = re.search(pattern, text, re.IGNORECASE)
			if match:
				err(f"{path.name}: possible licensed standard text - matched '{match.group(0)}'")

	for f in json.loads((DATA / "frameworks.json").read_text()):
		if f["framework_code"] != "NCC" and not f.get("attribution_note"):
			warn(f"framework {f['framework_code']} has no attribution note")


def check_no_server_scripts():
	for path in APP.rglob("*.json"):
		try:
			doc = json.loads(path.read_text())
		except (ValueError, UnicodeDecodeError):
			continue
		if isinstance(doc, dict) and doc.get("doctype") == "Server Script":
			err(f"server script committed: {path.relative_to(ROOT)}")


def check_python_syntax():
	for path in APP.rglob("*.py"):
		try:
			ast.parse(path.read_text())
		except SyntaxError as exc:
			err(f"syntax error in {path.relative_to(ROOT)}: {exc}")


def check_whitelisted_guards():
	"""Every @frappe.whitelist() must guard permissions somehow."""
	for path in APP.rglob("*.py"):
		source = path.read_text()
		if "@frappe.whitelist" not in source:
			continue
		tree = ast.parse(source)
		for node in ast.walk(tree):
			if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
				continue
			decorated = any(
				"whitelist" in ast.dump(d) for d in node.decorator_list
			)
			if not decorated:
				continue
			body = ast.get_source_segment(source, node) or ""
			guarded = any(
				token in body
				for token in ("_guard(", "has_permission", "check_permission", "frappe.only_for")
			)
			if not guarded:
				err(f"{path.relative_to(ROOT)}:{node.name} is whitelisted without a permission guard")


def check_hooks():
	source = (APP / "hooks.py").read_text()
	tree = ast.parse(source)
	assigned = {
		t.id
		for node in tree.body
		if isinstance(node, ast.Assign)
		for t in node.targets
		if isinstance(t, ast.Name)
	}
	for required in ("app_name", "app_title", "after_install", "after_migrate",
	                 "scheduler_events"):
		if required not in assigned:
			err(f"hooks.py is missing {required}")

	for match in re.finditer(r'"(neogrc\.[\w.]+)"', source):
		dotted = match.group(1)
		parts = dotted.split(".")[1:]
		# Trailing element is a function name; the module is everything before it.
		module_path = APP.joinpath(*parts[:-1]).with_suffix(".py")
		if not module_path.exists():
			pkg = APP.joinpath(*parts[:-1]) / "__init__.py"
			if not pkg.exists():
				err(f"hooks.py references a missing module: {dotted}")
				continue
			module_path = pkg
		if f"def {parts[-1]}" not in module_path.read_text():
			err(f"hooks.py references a missing function: {dotted}")


# --------------------------------------------------------------------------- #
def main() -> int:
	check_layout()
	check_python_syntax()
	check_patches()
	doctypes = check_doctypes()
	check_doctype_modules(doctypes)
	check_report_and_workspace_paths()
	check_external_links(doctypes)
	check_defaults(doctypes)
	stats = check_seed_data(doctypes)
	check_no_licensed_text()
	check_no_server_scripts()
	check_whitelisted_guards()
	check_hooks()

	print(f"doctypes: {len(doctypes)}  " + "  ".join(f"{k}: {v}" for k, v in stats.items()))

	for w in warnings:
		print(f"WARN  {w}")
	for e in errors:
		print(f"FAIL  {e}")

	if errors:
		print(f"\n{len(errors)} error(s)")
		return 1
	print(f"\nOK ({len(warnings)} warning(s))")
	return 0


if __name__ == "__main__":
	sys.exit(main())
