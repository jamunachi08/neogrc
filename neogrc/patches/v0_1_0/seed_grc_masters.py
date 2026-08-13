# Copyright (c) 2026, Neotec Integrated Solutions and contributors
# For license information, please see license.txt
"""Seed frameworks, controls, crosswalks and connectors.

Placed under [post_model_sync] because every seeder writes to DocTypes that
this same migrate creates. Running it pre-sync would fail on a fresh install
and, worse, would half-succeed on an upgrade.
"""

import frappe

from neogrc.setup.install import (
	configure_settings,
	create_roles,
	seed_all,
)


def execute():
	create_roles()
	seed_all()
	configure_settings()

	from neogrc import crosswalk

	crosswalk.clear_cache()
	frappe.db.commit()
