# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Control crosswalk.

The reference toolkit uses the Secure Controls Framework as a canonical control
vocabulary and maps everything else through it. This module implements the same
pivot, but the canonical framework is configurable, so a KSA customer can pivot
through the Neotec Canonical Controls set (shipped) or through their own
licensed SCF copy once imported.

Resolution is deliberately two-hop and nothing more:

    source control --(crosswalk)--> canonical --(crosswalk)--> target control

A finding evaluated against ``NCC-CRY-01`` therefore satisfies the NCA ECC,
ISO 27001 and NIST 800-53 controls that the canonical control maps to, without
the connector needing to know any of those frameworks exist.
"""

from __future__ import annotations

import frappe
from frappe.utils import now

CACHE_KEY = "neogrc_crosswalk_v2"

# Relationship strengths that are strong enough to carry coverage. A "related"
# edge says two controls are in the same territory, not that evidence for one
# demonstrates the other, so it is shown in the UI but excluded from scoring.
# Without this split, a single family-level derived mapping would mark dozens of
# target controls as covered off one passing check - exactly the coverage
# inflation the gap assessment is built to avoid.
BINDING_RELATIONSHIPS = {"equivalent", "subset", "superset"}
CACHE_TTL = 3600


def canonical_framework() -> str:
	fw = frappe.db.get_single_value("NeoGRC Settings", "canonical_framework")
	if fw:
		return fw
	fallback = frappe.db.get_value("NeoGRC Framework", {"is_canonical": 1}, "name")
	return fallback or "NCC"


def _load_edges() -> dict:
	"""Build the in-memory crosswalk graph.

	Returns ``{(framework, control_id): {target_framework: [(id, relationship)]}}``
	with every edge present in both directions, because a mapping recorded as
	NCC -> ISO must also resolve ISO -> NCC during gap assessment.
	"""
	cached = frappe.cache().get_value(CACHE_KEY)
	if cached:
		return cached

	edges: dict = {}
	rows = frappe.get_all(
		"NeoGRC Control Crosswalk",
		fields=[
			"source_control", "source_framework", "target_framework",
			"target_control_id", "relationship",
		],
		limit_page_length=0,
	)

	# source_control is named "<framework>::<control_id>"; derive the native id
	# rather than issuing one query per row.
	for row in rows:
		if not row.source_control or "::" not in row.source_control:
			continue
		src_fw, src_id = row.source_control.split("::", 1)
		src_fw = row.source_framework or src_fw
		rel = (row.relationship or "equivalent").lower()
		_add(edges, (src_fw, src_id), row.target_framework, row.target_control_id, rel)
		# The inverse of a subset is a superset; everything else is symmetric.
		inverse = {"subset": "superset", "superset": "subset"}.get(rel, rel)
		_add(edges, (row.target_framework, row.target_control_id), src_fw, src_id, inverse)

	frappe.cache().set_value(CACHE_KEY, edges, expires_in_sec=CACHE_TTL)
	return edges


def _add(edges: dict, key: tuple, framework: str, control_id: str, relationship: str):
	if not framework or not control_id:
		return
	bucket = edges.setdefault(key, {}).setdefault(framework, [])
	for i, (existing, rel) in enumerate(bucket):
		if existing == control_id:
			# Keep the strongest relationship if a pair is mapped more than once.
			if rel not in BINDING_RELATIONSHIPS and relationship in BINDING_RELATIONSHIPS:
				bucket[i] = (control_id, relationship)
			return
	bucket.append((control_id, relationship))


def clear_cache():
	frappe.cache().delete_value(CACHE_KEY)


def expand(framework: str, control_id: str, target_frameworks=None,
           include_related: bool = False) -> dict:
	"""Expand one control into every framework it maps to.

	Returns ``{framework: [(control_id, mapped_via)]}``. ``mapped_via`` is
	``None`` for a direct edge and the canonical control id when the mapping
	needed the canonical pivot, so reports can show how a control was reached.

	``include_related`` admits non-binding edges. It defaults to False so that
	scoring only ever follows mappings strong enough to carry evidence; the
	crosswalk viewer passes True to show the wider picture.
	"""
	edges = _load_edges()
	canonical = canonical_framework()
	targets = set(target_frameworks) if target_frameworks else None
	result: dict = {}

	def collect(fw, cid, via):
		if targets is not None and fw not in targets:
			return
		bucket = result.setdefault(fw, [])
		if not any(existing == cid for existing, _ in bucket):
			bucket.append((cid, via))

	# hop 0 - the control satisfies itself
	collect(framework, control_id, None)

	def admitted(rel):
		return include_related or rel in BINDING_RELATIONSHIPS

	# hop 1 - direct edges
	direct = edges.get((framework, control_id), {})
	for fw, entries in direct.items():
		for cid, rel in entries:
			if admitted(rel):
				collect(fw, cid, None)

	# hop 2 - pivot through canonical
	if framework == canonical:
		canonical_ids = [control_id]
	else:
		canonical_ids = [cid for cid, rel in direct.get(canonical, []) if admitted(rel)]

	for canonical_id in canonical_ids:
		for fw, entries in edges.get((canonical, canonical_id), {}).items():
			if fw == framework:
				continue
			for cid, rel in entries:
				if admitted(rel):
					collect(fw, cid, f"{canonical}::{canonical_id}")

	return result


def resolve_control(framework: str, control_id: str) -> str | None:
	"""Return the NeoGRC Control docname for a framework-native id, if it exists."""
	if not framework or not control_id:
		return None
	name = f"{framework}::{control_id}"
	return name if frappe.db.exists("NeoGRC Control", name) else None


@frappe.whitelist()
def map_control(framework: str, control_id: str, target_frameworks: str | None = None) -> dict:
	"""Whitelisted read of the crosswalk. Used by the Control form and reports."""
	frappe.has_permission("NeoGRC Control", throw=True)
	targets = None
	if target_frameworks:
		targets = [t.strip() for t in str(target_frameworks).split(",") if t.strip()]

	mapping = expand(framework, control_id, targets, include_related=True)
	binding = expand(framework, control_id, targets)
	out = []
	for fw in sorted(mapping):
		for cid, via in mapping[fw]:
			control = resolve_control(fw, cid)
			out.append(
				{
					"framework": fw,
					"control_id": cid,
					"control": control,
					"control_title": frappe.db.get_value("NeoGRC Control", control, "control_title")
					if control
					else None,
					"mapped_via": via,
					"resolved": bool(control),
					"carries_coverage": any(
						c == cid for c, _ in binding.get(fw, [])
					),
				}
			)
	return {
		"source": {"framework": framework, "control_id": control_id},
		"canonical_framework": canonical_framework(),
		"mappings": out,
		"framework_count": len(mapping),
		"generated_at": now(),
	}


@frappe.whitelist()
def find_conflicts(control: str) -> list:
	"""Surface mapped controls whose severity or automation posture disagrees.

	Multi-framework programmes routinely hit this: the same technical control is
	graded 'critical, manual' in one framework and 'medium, auto_fixable' in
	another. The conflict is not an error, but the stricter obligation is the
	one the customer must actually meet, so it is worth showing explicitly.
	"""
	frappe.has_permission("NeoGRC Control", throw=True)
	doc = frappe.get_cached_doc("NeoGRC Control", control)
	mapping = expand(doc.framework, doc.control_id)

	conflicts = []
	from .contract import SEVERITY_RANK

	base_rank = SEVERITY_RANK.get(doc.default_severity or "", 0)
	for fw, entries in mapping.items():
		if fw == doc.framework:
			continue
		for cid, via in entries:
			target = resolve_control(fw, cid)
			if not target:
				continue
			sev, auto = frappe.db.get_value(
				"NeoGRC Control", target, ["default_severity", "automation"]
			)
			target_rank = SEVERITY_RANK.get(sev or "", 0)
			if target_rank != base_rank or (auto or "") != (doc.automation or ""):
				conflicts.append(
					{
						"framework": fw,
						"control_id": cid,
						"control": target,
						"their_severity": sev,
						"our_severity": doc.default_severity,
						"their_automation": auto,
						"our_automation": doc.automation,
						"binding_severity": sev if target_rank > base_rank else doc.default_severity,
						"mapped_via": via,
					}
				)
	return conflicts
