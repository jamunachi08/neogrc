// Copyright (c) 2026, Neotec Integrated Solutions and contributors
// For license information, please see license.txt

frappe.query_reports["Findings by Control"] = {
	filters: [
		{ fieldname: "framework", label: __("Framework"), fieldtype: "Link", options: "NeoGRC Framework" },
		{ fieldname: "source", label: __("Connector"), fieldtype: "Link", options: "NeoGRC Connector" },
		{
			fieldname: "status",
			label: __("Evaluation Status"),
			fieldtype: "Select",
			options: "\nfail\ninconclusive\npass",
			default: "fail",
		},
		{ fieldname: "lookback_days", label: __("Lookback (days)"), fieldtype: "Int", default: 90 },
	],
};
