# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt

from . import __version__ as app_version  # noqa: F401

app_name = "neogrc"
app_title = "NeoGRC"
app_publisher = "Neotec Integrated Solutions"
app_description = (
	"Evidence-driven GRC engineering for ERPNext: connector findings, control "
	"crosswalks, automated evidence collection and gap assessment."
)
app_email = "support@neotec.ai"
app_license = "MIT"
# ERPNext is required, not optional. NeoGRC Vendor links to Supplier, NeoGRC Risk
# and NeoGRC Gap Assessment link to Company, and NeoGRC Policy Acknowledgment links
# to Employee. Frappe validates Link field targets when it inserts each DocType, so
# on a bare Frappe site install would fail partway through with those tables already
# created - a half-installed site rather than a clean refusal.
required_apps = ["frappe", "erpnext"]

# ---------------------------------------------------------------- Installation
after_install = "neogrc.setup.install.after_install"
after_migrate = "neogrc.setup.install.after_migrate"
before_uninstall = "neogrc.setup.install.before_uninstall"

# --------------------------------------------------------------------- Desk UI
app_include_js = "/assets/neogrc/js/neogrc.js"

doctype_js = {
	"NeoGRC Gap Assessment": "public/js/neogrc_gap_assessment.js",
	"NeoGRC Evidence Set": "public/js/neogrc_evidence_set.js",
	"NeoGRC Connector": "public/js/neogrc_connector.js",
	"NeoGRC Control": "public/js/neogrc_control.js",
}

# ------------------------------------------------------------------ Scheduling
scheduler_events = {
	"cron": {
		# Evidence sets are due-date driven, so a single hourly sweep is enough
		# and keeps runs off the busy nightly window.
		"0 * * * *": [
			"neogrc.setup.scheduler.run_due_evidence_sets",
		],
	},
	"daily_long": [
		"neogrc.setup.scheduler.expire_exceptions",
		"neogrc.setup.scheduler.flag_overdue_reviews",
		"neogrc.setup.scheduler.snapshot_metrics",
		"neogrc.setup.scheduler.purge_expired_artifacts",
	],
}

# ------------------------------------------------------------------ Permissions
permission_query_conditions = {}

has_permission = {}

# --------------------------------------------------------------------- Fixtures
fixtures = [
	{
		"dt": "Role",
		"filters": [["name", "in", ["NeoGRC Manager", "NeoGRC Engineer", "NeoGRC Auditor"]]],
	},
]

# -------------------------------------------------------------------- Overrides
override_doctype_dashboards = {}

# ------------------------------------------------------------------- Boot & CLI
extend_bootinfo = "neogrc.setup.install.boot_session"
