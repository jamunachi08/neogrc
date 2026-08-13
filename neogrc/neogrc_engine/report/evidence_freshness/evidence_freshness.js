// Copyright (c) 2026, Neotec Integrated Solutions and contributors
// For license information, please see license.txt

frappe.query_reports["Evidence Freshness"] = {
	filters: [
		{ fieldname: "connector", label: __("Connector"), fieldtype: "Link", options: "NeoGRC Connector" },
		{ fieldname: "include_disabled", label: __("Include Disabled"), fieldtype: "Check" },
	],
};
