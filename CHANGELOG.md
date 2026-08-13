# Changelog

## [0.2.2] - 2026-08-13

Second install fix. v0.2.1 got through DocType sync but failed in `post_install`.

### Fixed

- **`NeoGRC Settings.canonical_framework` shipped a Link default of `NCC`.**
  Frappe's `post_install` calls `init_singles()`, which saves every Single
  DocType *before* any `after_install` hook runs. Link validation therefore
  looked for framework `NCC` in a table that nothing had populated yet, and
  install died with `LinkValidationError` at 100% of DocType sync — with the app
  already added to `installed_apps`.

  The default and the `reqd` flag are both removed. `after_install` sets the
  value once the frameworks are seeded, and `crosswalk.canonical_framework()`
  already falls back to whichever framework carries `is_canonical`, so nothing
  depended on the default being present.

  This was flagged as a warning by `verify_tree.py` in v0.2.1 and wrongly
  dismissed on the assumption that Singles are created lazily. They are not.

### Added

- `after_migrate` now calls `configure_settings()` as well. If `after_install`
  ever fails partway through, `bench migrate` is the natural recovery command and
  should be able to finish the job rather than leaving Settings unconfigured.

### Changed — verifier

- A Link default on a **Single** DocType is now an error rather than a warning,
  with the reason stated: `init_singles()` runs before app hooks. Confirmed to
  reproduce this exact failure.
- A mandatory field on a Single now warns — `init_singles()` sets
  `ignore_mandatory`, but every later save will need a value.


## [0.2.1] - 2026-08-13

Install fixes found on a real bench. v0.2.0 could not complete `bench install-app`.

### Fixed

- **`NeoGRC Settings` failed to install.** The DocType is named "NeoGRC Settings"
  but its folder was `neogrc_engine_settings`, left over from the v0.2.0 rename.
  Frappe derives the controller module path from `scrub(doctype_name)`, so it
  looked for `neogrc.neogrc_engine.doctype.neogrc_settings` and raised
  `ImportError` partway through DocType sync — after 17 of 37 DocTypes had already
  been committed, leaving a half-installed site. Folder, `.json`, `.py` and
  `test_` files renamed to `neogrc_settings`.
- **`evidence_base_path` shipped a hardcoded default of `/home/frappe/grc-evidence`.**
  The Settings controller creates that directory at validate time, so on managed
  hosting `after_install` would have thrown `Cannot create the evidence directory`
  immediately after the DocTypes were committed. The default is removed; the path
  is now resolved at install to the site's private files directory.
- `configure_settings` passed `frappe.get_site_path(...)` straight into
  `evidence_base_path`, which returns a bench-relative path, while the controller
  requires an absolute one. Now wrapped in `os.path.abspath`.
- Workspace record renamed from "NeoGRC Engine" to "NeoGRC" so it matches its
  folder, and the post-install message no longer points at a workspace by its
  old name.

### Changed

- **ERPNext is now declared as a required app**, in both `hooks.required_apps` and
  `[tool.bench.frappe-dependencies]`. It was always required in practice —
  `NeoGRC Vendor` links to `Supplier`, `NeoGRC Risk` and `NeoGRC Gap Assessment`
  link to `Company`, `NeoGRC Policy Acknowledgment` links to `Employee` — and
  Frappe validates Link targets as it inserts each DocType, so a bare Frappe site
  would have failed partway through rather than refusing cleanly. The README no
  longer claims the app is standalone.

### Added — verifier

`verify_tree.py` now catches all four faults above, none of which it could see
before. Each check was confirmed to reproduce the failure it guards against.

- DocType folder name must equal `scrub(doctype_name)`, with the matching `.json`
  and `.py` present.
- Report and workspace folders must match their record names, their modules must
  be listed in `modules.txt`, script reports must have a controller, and
  `ref_doctype` must resolve.
- Link and Table targets outside the app must belong to a declared dependency;
  ERPNext targets error when `erpnext` is missing from `required_apps`.
- Field defaults are audited: Select defaults must be in their own options,
  numeric defaults must parse, and absolute filesystem paths are rejected outright.


## [0.2.0] - 2026-08-12

Renamed from `alphax_grc_engine` to **NeoGRC**, and enlarged the control
vocabulary from real catalogues.

### Changed — breaking

- App renamed `alphax_grc_engine` -> `neogrc`; module "GRC Engine" -> "NeoGRC Engine".
- **Every DocType is now prefixed `NeoGRC `.** This is not cosmetic. DocType
  names are globally unique per Frappe site, and the previous names collided with
  `alphax_grc` on `GRC Control`, `GRC Framework`, `GRC Policy`, `GRC Vendor` and
  `GRC Exception`, and with `grc_suite` on `GRC Policy` and `GRC Risk`. Installing
  two of them on one site would have broken migrate or silently shared schema.
- Roles renamed `NeoGRC Manager` / `NeoGRC Engineer` / `NeoGRC Auditor`, which
  collided the same way.
- `GRC Engine Settings` -> `NeoGRC Settings`.
- Crosswalk cache key bumped; `expand()` now filters by relationship strength.

### Added

- **Declarative query fetchers.** A fetcher can run a DocType query instead of a
  script: source DocType, JSON filters, and one of six enumerated pass
  conditions. Field names are validated against the DocType meta at save time;
  `Password` fieldtypes and credential-shaped fieldnames are refused. Nine query
  fetchers seeded in the new `ES-ERP-QUERY` set.
- `NeoGRC Policy Acknowledgment`: one row per person per policy version, issued
  automatically when a policy is approved. Only the subject can mark their own
  row acknowledged.
- Framework structure fields on `NeoGRC Control`: domain, subdomain, subcontrol
  hierarchy, applicability, reference-stub flag.
- EU GDPR (43 articles) and ISO/IEC 42001:2023 (38 controls) as frameworks.

### Added — testing

- `neogrc.demo.dataset` builds a demo programme for a fictional KSA entity,
  deliberately mixed so pass, fail, inconclusive, stale, exception-covered and
  uncovered controls are all represented. Tagged `neogrc-demo`; `purge` removes
  only tagged records. Blocked on sites with **Block Demo Data** set.
- `neogrc/demo/fixtures/` holds curl-ready ingest payloads, including eight
  documents that each break exactly one contract rule, for negative testing.
- `docs/NeoGRC-Test-Tour.docx` / `.pdf` / `.md`: a 15-page guided pass through
  every subsystem, with a quick-reference table of the ten cases where a silent
  pass would be worse than an obvious crash.
- Offline test suite grown to 63 assertions, now including the demo fixtures.

### Changed — data

- Control vocabulary grew from 298 to **702** across **15** frameworks:
  NCA ECC-2 34 -> 193, NIST CSF 2.0 1 -> 106, KSA PDPL 5 -> 31, ISO 22301 3 -> 36.
- Crosswalk edges 255 -> 1,121, of which 211 are binding and 910 are
  family-level derivations marked `related`.

### Fixed

- **The previous NCA ECC-2 identifiers were wrong.** They were constructed from
  the published structure, and assumed subdomain 2-7 was Cryptography when 2-7 is
  Data and Information Protection and 2-8 is Cryptography. Thirty-seven crosswalk
  edges had survived by format coincidence while pointing at unrelated controls.
  All edges into ECC-2, NIST CSF and PDPL were discarded and rebuilt.
- `expand()` now respects the `relationship` field, which was previously read
  and ignored. Without this, the derived family-level edges would have let one
  passing check mark dozens of target controls as covered.

### Notes on licensing

The NCA ECC-2 catalogue used as a structural source carries verbatim normative
Arabic text. That text is **not** reproduced here; only control codes, domain and
subdomain labels, and the subcontrol hierarchy were taken. ISO clause titles are
likewise excluded. NIST, GDPR and PDPL titles are public-domain government or
legislative text and are carried in full.


All notable changes to NeoGRC are recorded here.
This project follows semantic versioning.

## [0.1.0] - 2026-08-12

Initial release.

### Added

**Control backbone**
- `NeoGRC Framework`, `NeoGRC Control Family`, `NeoGRC Control`, `NeoGRC Control Crosswalk`.
- Neotec Canonical Controls (NCC): 62 controls across 16 families, original wording.
- 13 seeded frameworks: NCC, NCA ECC-2, NCA CCC, SAMA CSF, KSA PDPL, NIST 800-53 r5,
  ISO 27001:2022, ISO 22301:2019, SOC 2 TSC, PCI DSS 4.0, CIS v8, FedRAMP 20x KSI,
  NIST CSF 2.0.
- 255 crosswalk edges and 236 reference control stubs so gap assessment scores
  against a target framework before licensed control text is loaded.
- Two-hop crosswalk resolution pivoting through the configurable canonical framework,
  with a one-hour cache invalidated on any crosswalk or framework change.

**Finding data contract**
- `contract.py` implements the Finding schema v1.0.0, including the conditional rules
  that a failing evaluation must carry a message and a severity.
- `NeoGRC Finding` with a `NeoGRC Finding Evaluation` child table, worst-wins status rollup
  and severity rollup.
- `NeoGRC Narrative Finding` for cross-cutting findings.
- `api.ingest_findings` validates the whole batch before writing anything, and is
  idempotent on `(run_id, source, resource_id, resource_type)`.

**Evidence automation**
- `NeoGRC Connector`, `NeoGRC Evidence Fetcher`, `NeoGRC Evidence Set`, `NeoGRC Evidence Run`,
  `NeoGRC Evidence Artifact`.
- Evidence runner with preflight checks, structured exit codes (0/1/2/3/4/5),
  SHA-256 artifact hashing and per-run evidence directories.
- Shell fetchers execute with `shell=False` inside a resolved connector script root;
  path traversal and symlink escape are rejected.
- Validation rule engine with enumerated pass logic replacing the free-text
  `IF ... THEN PASS` strings used by the upstream fetcher catalogue.
- 8 connectors and 29 fetchers seeded. The `erpnext-internal` connector ships enabled
  with 11 Python handlers that need no credentials or egress.

**Assessment and reporting**
- `NeoGRC Gap Assessment` (submittable) with per-control results, separate coverage and
  compliance percentages, severity-weighted risk score and remediation effort.
- Multi-framework optimisation ranking canonical controls by risk closed per hour.
- Script Reports: Control Coverage Matrix, Findings by Control, Evidence Freshness,
  Multi Framework Optimisation.

**Programme records**
- `NeoGRC Risk` (5x5 inherent/residual scoring, banding, treatment gates),
  `NeoGRC Exception` (mandatory expiry, segregation-of-duties on approval),
  `NeoGRC Vendor` (tier-driven review cadence, PDPL DPA requirement),
  `NeoGRC Policy` (approval evidence gates), `NeoGRC Metric`.

**Platform**
- Roles: NeoGRC Manager, NeoGRC Engineer, NeoGRC Auditor.
- Scheduler: hourly due-set dispatch; daily exception expiry, overdue review alerts,
  metric snapshots and retention purge that protects evidence behind submitted
  assessments.
- Residency mode blocking non-local AI endpoints, and optional raw-attribute redaction.
- Patches under `[post_model_sync]`, idempotent `after_install` / `after_migrate`.
- `verify_tree.py` structural guard: field-order consistency, link and child-table
  targets, patch placement and resolution, hooks references, permission guards on
  every whitelisted method, seed-data referential integrity, no committed Server
  Scripts, and a licensed-text check over the seed data.
- `tests/test_offline_logic.py`: 34 assertions covering the contract validator and
  rule engine against a minimal Frappe stub. Runs without a bench or a database.

**Documentation**
- `README.md` covering install, the finding contract, crosswalk, gap assessment
  scoring, residency and the licensing stance.
- `docs/NeoGRC-Implementor-Manual.docx` and `.pdf` — 25-page branded
  implementor manual with five architecture diagrams, sources in `docs/figures/`.

### Notes
- No Server Scripts. All logic lives in versioned Python controllers.
- No normative control text from any licensed standard is reproduced. Framework
  records carry attribution notes; reference stubs are clearly marked as such.
