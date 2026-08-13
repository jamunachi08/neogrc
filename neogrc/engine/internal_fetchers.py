# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Built-in evidence fetchers for the Frappe/ERPNext site itself.

The site running this app is in scope for most of the frameworks it assesses,
and it is the one system where evidence needs no credentials, no egress and no
external tooling. These handlers make the app useful on the day it is installed
rather than after a cloud onboarding project.

Each handler returns ``(payload, resources)``. ``payload`` is the artifact that
gets hashed and stored; ``resources`` lets one fetcher emit a finding per
resource, which is what makes 'three users without 2FA' actionable instead of
'the 2FA check failed'.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, now

SYSTEM_USERS = ("Administrator", "Guest")


def _summary(**kw) -> dict:
	return {"collected_at": now(), "site": frappe.local.site, **kw}


# --------------------------------------------------------------------------- #
def two_factor_status(connector=None, fetcher=None):
	"""Which enabled desk users actually have 2FA in force. Maps to NCC IAM-02."""
	system_2fa = bool(frappe.db.get_single_value("System Settings", "enable_two_factor_auth"))

	users = frappe.get_all(
		"User",
		filters={"enabled": 1, "user_type": "System User", "name": ("not in", SYSTEM_USERS)},
		fields=["name", "full_name", "last_active"],
		limit_page_length=0,
	)

	resources, without = [], []
	for user in users:
		# A site-wide mandate covers the user; otherwise look for a per-user secret.
		has_secret = bool(
			frappe.db.exists("User", {"name": user.name, "bypass_restrict_ip_check_if_2fa_enabled": 1})
		)
		enforced = system_2fa or has_secret
		if not enforced:
			without.append(user.name)
		resources.append({
			"type": "frappe_user",
			"id": user.name,
			"raw": {"full_name": user.full_name, "last_active": str(user.last_active or ""),
			        "two_factor_enforced": enforced},
		})

	payload = _summary(
		system_two_factor_enabled=system_2fa,
		total_users=len(users),
		users_with_2fa=len(users) - len(without),
		users_without_2fa=len(without),
		non_compliant_users=without[:100],
	)
	return payload, resources


def privileged_access_review(connector=None, fetcher=None):
	"""Who holds System Manager and how stale that grant is. Maps to NCC IAM-03."""
	holders = frappe.get_all(
		"Has Role",
		filters={"role": "System Manager", "parenttype": "User"},
		fields=["parent as user", "creation"],
		limit_page_length=0,
	)

	active, resources = [], []
	for row in holders:
		if row.user in SYSTEM_USERS:
			continue
		enabled, last_active = frappe.db.get_value("User", row.user, ["enabled", "last_active"])
		if not enabled:
			continue
		dormant = bool(last_active and str(last_active) < str(add_to_date(now(), days=-90)))
		active.append(row.user)
		resources.append({
			"type": "frappe_privileged_user",
			"id": row.user,
			"raw": {"granted_on": str(row.creation), "last_active": str(last_active or ""),
			        "dormant_90d": dormant},
		})

	dormant_count = sum(1 for r in resources if r["raw"]["dormant_90d"])
	payload = _summary(
		system_managers=len(active),
		dormant_system_managers=dormant_count,
		users=active,
	)
	return payload, resources


def disabled_user_access_removal(connector=None, fetcher=None):
	"""Disabled users that still carry roles. Maps to NCC IAM-05."""
	rows = frappe.db.sql(
		"""
		SELECT u.name AS user, COUNT(r.name) AS role_count
		FROM `tabUser` u
		INNER JOIN `tabHas Role` r ON r.parent = u.name AND r.parenttype = 'User'
		WHERE u.enabled = 0 AND u.user_type = 'System User'
		GROUP BY u.name
		""",
		as_dict=True,
	)
	resources = [
		{"type": "frappe_user", "id": r.user, "raw": {"residual_roles": r.role_count}}
		for r in rows
	]
	payload = _summary(
		disabled_users_with_roles=len(rows),
		detail=[{"user": r.user, "roles": r.role_count} for r in rows[:100]],
	)
	return payload, resources


def audit_log_configuration(connector=None, fetcher=None):
	"""Document versioning and activity logging posture. Maps to NCC LOG-01/LOG-02."""
	settings = frappe.get_single("System Settings")
	tracked = frappe.get_all(
		"DocType", filters={"track_changes": 1, "istable": 0}, pluck="name", limit_page_length=0
	)
	recent_activity = frappe.db.count(
		"Activity Log", {"creation": (">", add_to_date(now(), days=-7))}
	)
	oldest = frappe.db.get_value("Version", {}, "creation", order_by="creation asc")

	payload = _summary(
		doctypes_with_change_tracking=len(tracked),
		activity_log_entries_7d=recent_activity,
		oldest_version_record=str(oldest or ""),
		logging_active=1 if recent_activity > 0 else 0,
		login_log_retention_days=int(getattr(settings, "logout_on_password_reset", 0) or 0),
	)
	return payload, []


def failed_login_monitoring(connector=None, fetcher=None):
	"""Failed authentication volume and lockout configuration. Maps to NCC LOG-03."""
	settings = frappe.get_single("System Settings")
	window = add_to_date(now(), days=-7)

	failures = frappe.get_all(
		"Activity Log",
		filters={"status": "Failed", "operation": "Login", "creation": (">", window)},
		fields=["user", "ip_address", "creation"],
		limit_page_length=0,
	)

	by_user: dict = {}
	for row in failures:
		by_user[row.user or "unknown"] = by_user.get(row.user or "unknown", 0) + 1

	payload = _summary(
		failed_logins_7d=len(failures),
		distinct_accounts=len(by_user),
		lockout_threshold=int(getattr(settings, "allow_consecutive_login_attempts", 0) or 0),
		lockout_duration=int(getattr(settings, "allow_login_after_fail", 0) or 0),
		top_accounts=sorted(by_user.items(), key=lambda kv: -kv[1])[:10],
	)
	return payload, []


def password_policy(connector=None, fetcher=None):
	"""Password strength enforcement. Maps to NCC IAM-06."""
	settings = frappe.get_single("System Settings")
	score = int(getattr(settings, "minimum_password_score", 0) or 0)
	payload = _summary(
		minimum_password_score=score,
		force_user_to_reset_password_days=int(
			getattr(settings, "force_user_to_reset_password", 0) or 0
		),
		session_expiry=getattr(settings, "session_expiry", ""),
		two_factor_enabled=int(bool(getattr(settings, "enable_two_factor_auth", 0))),
	)
	return payload, []


def backup_status(connector=None, fetcher=None):
	"""Most recent backup age. Maps to NCC DAT-03."""
	last = frappe.db.get_value(
		"File",
		{"file_name": ("like", "%-database.sql.gz")},
		["file_name", "creation", "file_size"],
		order_by="creation desc",
		as_dict=True,
	)

	age_hours = None
	if last and last.creation:
		from frappe.utils import time_diff_in_hours

		age_hours = round(time_diff_in_hours(now(), last.creation), 1)

	payload = _summary(
		last_backup_file=(last or {}).get("file_name", ""),
		last_backup_at=str((last or {}).get("creation", "")),
		last_backup_size_bytes=int((last or {}).get("file_size") or 0),
		backup_age_hours=age_hours if age_hours is not None else -1,
		backup_found=1 if last else 0,
	)
	return payload, []


def installed_app_inventory(connector=None, fetcher=None):
	"""Application inventory for the site. Maps to NCC AST-01."""
	apps = []
	for app in frappe.get_installed_apps():
		apps.append({
			"app": app,
			"version": frappe.get_attr(f"{app}.__version__") if _has_version(app) else "unknown",
		})
	payload = _summary(installed_apps=len(apps), apps=apps)
	resources = [
		{"type": "frappe_app", "id": a["app"], "raw": a} for a in apps
	]
	return payload, resources


def _has_version(app: str) -> bool:
	try:
		frappe.get_attr(f"{app}.__version__")
		return True
	except Exception:
		return False


def exception_expiry_check(connector=None, fetcher=None):
	"""Expired-but-still-approved exceptions. Maps to NCC GOV-05."""
	from frappe.utils import nowdate

	rows = frappe.get_all(
		"NeoGRC Exception",
		filters={"status": "approved", "expires_at": ("<", nowdate())},
		fields=["name", "title", "expires_at", "owner_user"],
		limit_page_length=0,
	)
	resources = [
		{"type": "neogrc_exception", "id": r.name, "raw": dict(r)} for r in rows
	]
	payload = _summary(expired_open_exceptions=len(rows), detail=[dict(r) for r in rows[:50]])
	return payload, resources


def policy_review_currency(connector=None, fetcher=None):
	"""Approved policies past their review date. Maps to NCC GOV-02."""
	from frappe.utils import nowdate

	overdue = frappe.get_all(
		"NeoGRC Policy",
		filters={"status": "approved", "next_review_at": ("<", nowdate())},
		fields=["name", "title", "next_review_at", "owner_user"],
		limit_page_length=0,
	)
	total = frappe.db.count("NeoGRC Policy", {"status": "approved"})
	payload = _summary(
		approved_policies=total,
		overdue_reviews=len(overdue),
		detail=[dict(r) for r in overdue[:50]],
	)
	resources = [{"type": "neogrc_policy", "id": r.name, "raw": dict(r)} for r in overdue]
	return payload, resources


def vendor_review_currency(connector=None, fetcher=None):
	"""Active vendors past their review date. Maps to NCC TPR-02."""
	from frappe.utils import nowdate

	overdue = frappe.get_all(
		"NeoGRC Vendor",
		filters={"status": "active", "next_review_at": ("<", nowdate())},
		fields=["name", "vendor_name", "tier", "next_review_at"],
		limit_page_length=0,
	)
	payload = _summary(
		active_vendors=frappe.db.count("NeoGRC Vendor", {"status": "active"}),
		overdue_reviews=len(overdue),
		critical_overdue=sum(1 for v in overdue if v.tier == "critical"),
		detail=[dict(v) for v in overdue[:50]],
	)
	resources = [{"type": "neogrc_vendor", "id": v.name, "raw": dict(v)} for v in overdue]
	return payload, resources
