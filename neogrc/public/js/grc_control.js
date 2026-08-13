// Copyright (c) 2026, Neotec Integrated Solutions and contributors
// For license information, please see license.txt

frappe.ui.form.on("NeoGRC Control", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Show Crosswalk"), () => {
			frappe
				.call({
					method: "neogrc.api.map_control",
					args: { framework: frm.doc.framework, control_id: frm.doc.control_id },
				})
				.then((r) => {
					frappe.msgprint({
						title: __("{0} across frameworks", [frm.doc.control_id]),
						message: neogrc.render_crosswalk(r.message),
						wide: true,
					});
				});
		});

		frm.add_custom_button(__("Conflicting Requirements"), () => {
			frappe
				.call({
					method: "neogrc.crosswalk.find_conflicts",
					args: { control: frm.doc.name },
				})
				.then((r) => {
					const rows = r.message || [];
					if (!rows.length) {
						frappe.msgprint(__("No severity or automation conflicts across mapped frameworks."));
						return;
					}
					const body = rows
						.map(
							(c) =>
								`<tr><td>${frappe.utils.escape_html(c.framework)}</td>
								 <td>${frappe.utils.escape_html(c.control_id)}</td>
								 <td>${frappe.utils.escape_html(c.our_severity || "")} &rarr; ${frappe.utils.escape_html(
									c.their_severity || ""
								)}</td>
								 <td><b>${frappe.utils.escape_html(c.binding_severity || "")}</b></td></tr>`
						)
						.join("");
					frappe.msgprint({
						title: __("Conflicting requirements"),
						wide: true,
						message: `<p class="text-muted">${__(
							"Where frameworks disagree, the stricter obligation binds."
						)}</p>
						<table class="table table-bordered table-sm"><thead><tr>
							<th>${__("Framework")}</th><th>${__("Control")}</th>
							<th>${__("Severity")}</th><th>${__("Binding")}</th>
						</tr></thead><tbody>${body}</tbody></table>`,
					});
				});
		}, __("Analyse"));

		frm.add_custom_button(__("Related Findings"), () => {
			frappe.set_route("List", "NeoGRC Finding", { "NeoGRC Finding Evaluation": ["control", "=", frm.doc.name] });
		}, __("View"));
	},
});
