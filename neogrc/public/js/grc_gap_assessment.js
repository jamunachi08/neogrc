// Copyright (c) 2026, Neotec Integrated Solutions and contributors
// For license information, please see license.txt

frappe.ui.form.on("NeoGRC Gap Assessment", {
	refresh(frm) {
		frm.trigger("set_status_indicator");

		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			const running = ["Queued", "Running"].includes(frm.doc.status);
			frm.add_custom_button(running ? __("Refresh Status") : __("Run Assessment"), () => {
				if (running) return frm.reload_doc();
				frm.call("run").then(() => frm.reload_doc());
			}).toggleClass("btn-primary", !running);
		}

		if (frm.doc.status === "Completed") {
			frm.add_custom_button(__("Optimisation Plan"), () => {
				frappe.set_route("query-report", "Multi Framework Optimisation", {
					assessment: frm.doc.name,
				});
			}, __("View"));

			frm.add_custom_button(__("Failing Controls"), () => {
				frm.set_query_filter_results("fail");
			}, __("View"));
		}

		frm.trigger("render_scorecard");
	},

	set_status_indicator(frm) {
		const map = {
			Draft: "gray", Queued: "blue", Running: "blue",
			Completed: "green", Failed: "red",
		};
		if (frm.doc.status) {
			frm.page.set_indicator(__(frm.doc.status), map[frm.doc.status] || "gray");
		}
	},

	render_scorecard(frm) {
		if (frm.doc.status !== "Completed") return;

		// Coverage and compliance answer different questions and are shown
		// side by side deliberately: high compliance over low coverage means
		// very little has actually been tested.
		const cards = [
			{ label: __("Automated Coverage"), value: `${(frm.doc.coverage_pct || 0).toFixed(1)}%` },
			{ label: __("Compliance"), value: `${(frm.doc.compliance_pct || 0).toFixed(1)}%` },
			{ label: __("Failing"), value: frm.doc.controls_fail || 0 },
			{ label: __("Not Covered"), value: frm.doc.controls_not_covered || 0 },
			{ label: __("Weighted Risk"), value: frm.doc.weighted_risk_score || 0 },
			{ label: __("Effort (h)"), value: (frm.doc.remediation_effort_hours || 0).toFixed(0) },
		];

		const html = `<div class="row" style="margin-bottom:12px">${cards
			.map(
				(c) => `<div class="col-sm-2 col-xs-4" style="text-align:center">
					<div style="font-size:1.6em;font-weight:600">${c.value}</div>
					<div class="text-muted small">${c.label}</div>
				</div>`
			)
			.join("")}</div>`;

		frm.dashboard.clear_headline();
		frm.dashboard.set_headline(html);
	},
});
