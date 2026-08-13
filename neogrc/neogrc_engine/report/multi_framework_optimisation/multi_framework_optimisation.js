// Copyright (c) 2026, Neotec Integrated Solutions and contributors
// For license information, please see license.txt

frappe.query_reports["Multi Framework Optimisation"] = {
	filters: [
		{
			fieldname: "assessment",
			label: __("Gap Assessment"),
			fieldtype: "Link",
			options: "NeoGRC Gap Assessment",
			reqd: 1,
			get_query: () => ({ filters: { status: "Completed" } }),
		},
	],
};
