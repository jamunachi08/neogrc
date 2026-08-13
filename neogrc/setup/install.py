# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Install and migrate hooks.

Every seeder here is idempotent: it creates what is missing and leaves anything
the customer has edited alone. That rule matters more than it looks. A migrate
that resets a control's severity or overwrites a locally-authored crosswalk
silently changes audit conclusions, and nobody would notice until the next
assessment produced different numbers with no changelog entry explaining why.
"""

from __future__ import annotations

import json
import os

import frappe
from frappe import _

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

ROLES = {
	"NeoGRC Manager": "Owns the GRC programme. Full control over frameworks, risks, policies and assessments.",
	"NeoGRC Engineer": "Builds and runs evidence automation. Cannot delete audit records.",
	"NeoGRC Auditor": "Read-only access to controls, findings, evidence and assessments.",
}


def after_install():
	create_roles()
	seed_all()
	configure_settings()
	frappe.db.commit()
	frappe.msgprint(
		_("NeoGRC installed. Open the NeoGRC workspace to configure connectors."),
		alert=True,
	)


def after_migrate():
	create_roles()
	seed_all()
	# Also run here, not just on install. If after_install ever fails partway
	# through, `bench migrate` is the natural recovery command and must be able
	# to finish the job rather than leaving Settings unconfigured.
	configure_settings()
	from .. import crosswalk

	crosswalk.clear_cache()
	frappe.db.commit()


def before_uninstall():
	"""Warn loudly. Evidence is an audit record, not application state."""
	findings = frappe.db.count("NeoGRC Finding")
	runs = frappe.db.count("NeoGRC Evidence Run")
	if findings or runs:
		frappe.msgprint(
			_("Uninstalling will delete {0} findings and {1} evidence runs. "
			  "Export them first if they back a completed audit.").format(findings, runs),
			indicator="red",
		)


# --------------------------------------------------------------------------- #
def create_roles():
	for role, desc in ROLES.items():
		if frappe.db.exists("Role", role):
			continue
		frappe.get_doc({
			"doctype": "Role",
			"role_name": role,
			"desk_access": 1,
			"description": desc,
		}).insert(ignore_permissions=True)


def configure_settings():
	settings = frappe.get_single("NeoGRC Settings")
	changed = False

	if not settings.canonical_framework and frappe.db.exists("NeoGRC Framework", "NCC"):
		settings.canonical_framework = "NCC"
		changed = True

	if not settings.evidence_base_path:
		# get_site_path returns a bench-relative path ("sitename/private/..."),
		# but the Settings controller requires an absolute one. Passing it through
		# unresolved makes after_install throw immediately after the DocTypes are
		# already committed, which leaves a half-installed site.
		settings.evidence_base_path = os.path.abspath(
			frappe.get_site_path("private", "files", "neogrc-evidence")
		)
		changed = True

	if changed:
		settings.flags.ignore_permissions = True
		settings.save()


# --------------------------------------------------------------------------- #
def seed_all():
	seed_frameworks()
	seed_control_families()
	seed_controls()
	seed_crosswalks()
	seed_connectors()


def _load(filename: str):
	path = os.path.join(DATA_DIR, filename)
	if not os.path.exists(path):
		return []
	with open(path, encoding="utf-8") as fh:
		return json.load(fh)


def _insert_if_missing(doctype: str, name: str, payload: dict) -> bool:
	if frappe.db.exists(doctype, name):
		return False
	doc = frappe.new_doc(doctype)
	doc.update(payload)
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = False
	doc.insert()
	return True


def seed_frameworks():
	for row in _load("frameworks.json"):
		_insert_if_missing("NeoGRC Framework", row["framework_code"], row)


def seed_control_families():
	for row in _load("control_families.json"):
		name = f"{row['framework']}::{row['family_code']}"
		_insert_if_missing("NeoGRC Control Family", name, row)


def seed_controls():
	for row in _load("controls.json"):
		name = f"{row['framework']}::{row['control_id']}"
		if row.get("family") and not frappe.db.exists("NeoGRC Control Family", row["family"]):
			row.pop("family")
		_insert_if_missing("NeoGRC Control", name, row)


def seed_crosswalks():
	"""Seed crosswalk edges.

	Uniqueness is by (source_control, target_framework, target_control_id)
	rather than by docname, because crosswalk records are hash-named.
	"""
	created = 0
	for row in _load("crosswalks.json"):
		source = f"{row['source_framework']}::{row['source_control_id']}"
		if not frappe.db.exists("NeoGRC Control", source):
			continue
		if not frappe.db.exists("NeoGRC Framework", row["target_framework"]):
			continue
		exists = frappe.db.exists("NeoGRC Control Crosswalk", {
			"source_control": source,
			"target_framework": row["target_framework"],
			"target_control_id": row["target_control_id"],
		})
		if exists:
			continue
		doc = frappe.new_doc("NeoGRC Control Crosswalk")
		doc.source_control = source
		doc.target_framework = row["target_framework"]
		doc.target_control_id = row["target_control_id"]
		doc.relationship = row.get("relationship", "equivalent")
		doc.confidence = row.get("confidence", "high")
		doc.source_of_mapping = row.get("source_of_mapping", "Manual")
		doc.notes = row.get("notes")
		doc.flags.ignore_permissions = True
		doc.insert()
		created += 1
	return created


def seed_connectors():
	"""Seed the connector catalogue and its fetchers, disabled by default.

	Connectors ship disabled because enabling one implies credentials exist and
	egress is permitted. That is a decision for the site owner, not the
	installer.
	"""
	for row in _load("connectors.json"):
		_insert_if_missing("NeoGRC Connector", row["connector_id"], row)

	for row in _load("fetchers.json"):
		if not frappe.db.exists("NeoGRC Connector", row.get("connector")):
			continue
		payload = dict(row)
		controls = payload.pop("controls", [])
		rules = payload.pop("validation_rules", [])
		if frappe.db.exists("NeoGRC Evidence Fetcher", payload["fetcher_id"]):
			continue
		doc = frappe.new_doc("NeoGRC Evidence Fetcher")
		doc.update(payload)
		for c in controls:
			doc.append("controls", c)
		for r in rules:
			doc.append("validation_rules", r)
		doc.flags.ignore_permissions = True
		doc.insert()

	for row in _load("evidence_sets.json"):
		payload = dict(row)
		fetchers = payload.pop("fetchers", [])
		if frappe.db.exists("NeoGRC Evidence Set", payload["set_id"]):
			continue
		available = [f for f in fetchers if frappe.db.exists("NeoGRC Evidence Fetcher", f["fetcher"])]
		if not available:
			continue
		doc = frappe.new_doc("NeoGRC Evidence Set")
		doc.update(payload)
		for f in available:
			doc.append("fetchers", f)
		doc.flags.ignore_permissions = True
		doc.insert()


# --------------------------------------------------------------------------- #
def boot_session(bootinfo):
	if not set(ROLES) & set(frappe.get_roles()):
		return
	bootinfo.neogrc = {
		"canonical_framework": frappe.db.get_single_value(
			"NeoGRC Settings", "canonical_framework"
		),
		"residency_mode": frappe.db.get_single_value("NeoGRC Settings", "residency_mode"),
	}
