// Copyright (c) 2026, Neotec Integrated Solutions and contributors
// For license information, please see license.txt

frappe.provide("neogrc");

neogrc.SEVERITY_COLOUR = {
	critical: "red",
	high: "orange",
	medium: "yellow",
	low: "blue",
	info: "gray",
};

neogrc.STATUS_COLOUR = {
	pass: "green",
	fail: "red",
	inconclusive: "orange",
	not_applicable: "gray",
	not_covered: "red",
	skipped: "gray",
};

/** Render a crosswalk result set as a grouped HTML table. */
neogrc.render_crosswalk = function (result) {
	if (!result.mappings || !result.mappings.length) {
		return `<p class="text-muted">${__("No crosswalk mappings recorded for this control.")}</p>`;
	}

	const grouped = {};
	result.mappings.forEach((m) => {
		if (m.framework === result.source.framework) return;
		(grouped[m.framework] = grouped[m.framework] || []).push(m);
	});

	const frameworks = Object.keys(grouped).sort();
	if (!frameworks.length) {
		return `<p class="text-muted">${__("This control does not yet map to another framework.")}</p>`;
	}

	let html = `<table class="table table-bordered table-sm">
		<thead><tr>
			<th>${__("Framework")}</th><th>${__("Control")}</th><th>${__("Resolved")}</th><th>${__("Via")}</th>
		</tr></thead><tbody>`;

	frameworks.forEach((fw) => {
		grouped[fw].forEach((m, i) => {
			const label = frappe.utils.escape_html(m.control_id);
			const title = m.control_title ? ` &mdash; ${frappe.utils.escape_html(m.control_title)}` : "";
			const cell = m.control
				? `<a href="/app/grc-control/${encodeURIComponent(m.control)}">${label}</a>${title}`
				: `${label}${title}`;
			html += `<tr>
				<td>${i === 0 ? frappe.utils.escape_html(fw) : ""}</td>
				<td>${cell}</td>
				<td>${m.resolved ? "&#10003;" : `<span class="text-muted">${__("stub")}</span>`}</td>
				<td class="text-muted small">${m.mapped_via ? frappe.utils.escape_html(m.mapped_via) : __("direct")}</td>
			</tr>`;
		});
	});

	return html + "</tbody></table>";
};
