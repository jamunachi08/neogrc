// Copyright (c) 2026, Neotec Integrated Solutions and contributors
// For license information, please see license.txt

frappe.ui.form.on("NeoGRC Connector", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Preflight Check"), () => {
			frm.call("preflight").then((r) => {
				const res = r.message || {};
				frappe.msgprint({
					title: __("Preflight"),
					indicator: res.ready ? "green" : "red",
					message: res.ready
						? __("Connector is ready: binaries, credentials and script root all resolve.")
						: `${frappe.utils.escape_html(res.reason)}<br><br><span class="text-muted">${__(
								"Exit code"
						  )}: ${res.exit_code}</span>`,
				});
			});
		});

		frm.add_custom_button(__("Fetchers"), () => {
			frappe.set_route("List", "NeoGRC Evidence Fetcher", { connector: frm.doc.name });
		}, __("View"));

		if (frm.doc.consecutive_failures > 2) {
			frm.dashboard.add_comment(
				__("{0} consecutive failed runs. Check credentials before the next scheduled run.", [
					frm.doc.consecutive_failures,
				]),
				"red",
				true
			);
		}
	},
});
