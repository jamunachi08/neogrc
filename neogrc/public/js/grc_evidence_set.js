// Copyright (c) 2026, Neotec Integrated Solutions and contributors
// For license information, please see license.txt

frappe.ui.form.on("NeoGRC Evidence Set", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Run Now"), () => {
			frappe.confirm(
				__("Queue an evidence run for {0}?", [frm.doc.set_name]),
				() => {
					frm.call("run_now").then((r) => {
						if (r.message) frappe.set_route("Form", "NeoGRC Evidence Run", r.message);
					});
				}
			);
		}).addClass("btn-primary");

		frm.add_custom_button(__("Recent Runs"), () => {
			frappe.set_route("List", "NeoGRC Evidence Run", { evidence_set: frm.doc.name });
		}, __("View"));

		if (frm.doc.last_run) {
			frm.dashboard.add_comment(
				__("Last run {0}", [frappe.datetime.comment_when(frm.doc.last_run)]),
				"blue",
				true
			);
		}
	},
});
