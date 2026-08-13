// Copyright (c) 2026, Neotec Integrated Solutions and contributors
// For license information, please see license.txt

frappe.query_reports["Control Coverage Matrix"] = {
	filters: [
		{
			fieldname: "framework",
			label: __("Framework"),
			fieldtype: "Link",
			options: "NeoGRC Framework",
			reqd: 1,
			default: frappe.boot.grc_engine ? frappe.boot.grc_engine.canonical_framework : "NCC",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status") {
			const colour = {
				Automated: "green",
				"Fetcher disabled": "orange",
				"Not automatable": "red",
			}[data.status];
			if (colour) value = `<span style="color:var(--text-on-${colour}, inherit)">${value}</span>`;
		}
		return value;
	},
};
