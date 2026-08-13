# NeoGRC

Evidence-driven governance, risk and compliance for Frappe/ERPNext v15.

Connectors collect technical evidence. Evidence is validated and turned into
**Findings** that satisfy a versioned data contract. Findings are scored against
a **control crosswalk** so one piece of evidence answers many frameworks at
once. Gap assessments turn the result into a position you can put in front of an
auditor.

Version 0.1.0 · MIT · Neotec Integrated Solutions

---

## What it does

```
  connectors collect evidence
          │
          ▼
  validation rules  ──►  Findings  (contract v1.0.0)
          │
          ▼
  crosswalk expansion (canonical control pivot)
          │
          ▼
  gap assessment ──► scorecard, remediation plan, optimisation ranking
```

The design point is that a connector never needs to know which frameworks you
report against. It emits `NCC CRY-01: fail`, and the crosswalk resolves that into
NCA ECC, ISO 27001, NIST 800-53, PCI DSS and FedRAMP 20x simultaneously.

## Requirements

Frappe v15 **and ERPNext v15**. ERPNext is not optional: `NeoGRC Vendor` links to
`Supplier`, `NeoGRC Risk` and `NeoGRC Gap Assessment` link to `Company`, and
`NeoGRC Policy Acknowledgment` links to `Employee`. Frappe validates Link field
targets as it inserts each DocType, so installing on a bare Frappe site fails
partway through and leaves a half-installed site rather than refusing cleanly.

Background workers and the scheduler must be running — evidence collection and
gap assessment are queued jobs.

## Install

```bash
cd ~/frappe-bench
bench get-app neogrc /path/to/neogrc
bench --site yoursite install-app neogrc
bench --site yoursite migrate
```

Then open the **GRC Engine** workspace.

Install seeds 15 frameworks, 702 controls, 1,121 crosswalk edges,
9 connectors and 38 fetchers. Every seeder is idempotent, so `bench migrate`
never overwrites anything you have edited.

### First evidence run, no credentials needed

The `erpnext-internal` connector ships enabled and collects evidence from the
site itself — MFA enforcement, privileged access, audit logging, backup
freshness, policy and vendor review currency.

1. Open **NeoGRC Evidence Set** → `ES-ERP-BASELINE`
2. Click **Run Now**
3. Open the resulting **NeoGRC Evidence Run** to see artifacts, hashes and findings
4. Create a **NeoGRC Gap Assessment**, add framework `NCC`, and click **Run Assessment**

## Roles

| Role | Can |
|---|---|
| **NeoGRC Manager** | Everything, including deleting audit records |
| **NeoGRC Engineer** | Build connectors and fetchers, run collection and assessments; cannot delete |
| **NeoGRC Auditor** | Read-only across controls, findings, evidence and assessments |

## The Finding contract

Every connector emits documents matching contract v1.0.0. One document is one
resource with one or more control evaluations.

```json
{
  "schema_version": "1.0.0",
  "source": "aws-inspector",
  "source_version": "2026.04.01",
  "run_id": "01HXKJ...",
  "collected_at": "2026-04-13T15:04:05Z",
  "resource": {
    "type": "aws_s3_bucket",
    "id": "acme-prod-logs",
    "arn": "arn:aws:s3:::acme-prod-logs",
    "region": "us-east-1"
  },
  "evaluations": [
    {
      "control_framework": "NCC",
      "control_id": "CRY-01",
      "status": "fail",
      "severity": "high",
      "message": "Bucket has no default encryption configured",
      "remediation": {
        "summary": "Enable SSE-KMS with a customer-managed key",
        "effort_hours": 1,
        "automation": "auto_fixable"
      }
    }
  ]
}
```

Post a batch:

```bash
curl -X POST https://yoursite/api/method/neogrc.api.ingest_findings \
  -H "Authorization: token <api_key>:<api_secret>" \
  -H "Content-Type: application/json" \
  -d '{"findings": [ ... ], "dry_run": 1}'
```

`dry_run: 1` validates without writing. The whole batch is validated before
anything is persisted — a partially ingested run is worse than a rejected one,
because a gap assessment over half a connector's output silently reports
controls as uncovered.

Ingestion is idempotent on `(run_id, source, resource_id, resource_type)`.

**Status and severity are independent.** `status` is pass/fail. `severity` is
the impact if the control were failing. A failing low-severity control is real,
just not urgent. `inconclusive` means the tool *tried and could not determine* —
a dropped API call, a missing permission — and is never treated as a pass.

## Evidence collection

A **Connector** describes a source: handler type, auth mode, required binaries
and environment variables, cache TTL. A **Fetcher** is one check within it,
carrying its control mappings and validation rules. An **Evidence Set** groups
fetchers and gives them a schedule.

Preflight runs before the first fetcher, so a missing `aws` CLI produces one
clear exit-code-5 message rather than thirty opaque failures.

Structured exit codes: `0` success · `1` generic · `2` auth · `3` rate limited ·
`4` partial · `5` dependency missing.

### Declarative query fetchers

A fetcher can run a **query** instead of a script. Set Handler Mode to `Query`
and give it a DocType, a Frappe filter object, and a pass condition:

| Condition | Passes when |
|---|---|
| `COUNT_EQUALS_ZERO` | Nothing matched the filters |
| `COUNT_LTE_THRESHOLD` | Match count is at or below the threshold |
| `COUNT_GTE_THRESHOLD` | At least N records exist |
| `ALL_ROWS_FIELD_SET` | Every matched row has a value in the checked field |
| `ALL_ROWS_FIELD_EQUALS` | Every matched row equals the expected value |
| `NO_ROW_OLDER_THAN_DAYS` | No matched row is older than the tolerance |

Filters support `"@today"` and `["<", "@days_ago:90"]`, so freshness checks do
not need editing every day.

Field names in filters and conditions are validated against the target DocType's
own meta at save time, not at run time, so a typo fails in front of the person
writing it rather than at 2am inside a scheduled run. Fields of type `Password`,
and fieldnames like `api_secret` or `token`, are refused outright — evidence
artifacts are hashed, attached and retained, and a credential must not become a
permanent audit record.

Each failing row becomes a resource on the finding, so the output is a task list
rather than a status. Nine query fetchers ship in `ES-ERP-QUERY`.

### Validation rules

The upstream Paramify catalogue stores rule logic as free text
(`IF match.group(1) == match.group(2) THEN PASS`) and evaluates it in Python.
Executing operator-authored strings inside a Frappe site is not acceptable, so
here the logic is an enumerated Select and the engine dispatches on it. The
expressiveness lost is small; the code-execution surface removed is not.

Rule types: Regex Capture, JSON Path, JSON Key Equals, Non-Empty Output.

**A fetcher with no validation rule records `inconclusive`, never `pass`.**
Collecting a file is not the same as demonstrating a control.

### Shell fetcher sandboxing

Shell handlers run with `shell=False` against a path resolved inside the
connector's declared `script_root`. Absolute paths, `..` traversal and symlinks
escaping the root are all rejected. A Fetcher record is operator-supplied data,
so it must not become a remote-code-execution primitive for anyone with desk
access. Shell connectors ship disabled with no script root set.

## Crosswalk

Resolution is two-hop and no more:

```
source control ──(crosswalk)──► canonical ──(crosswalk)──► target control
```

The canonical framework is configurable in **NeoGRC Settings**. It ships as
**NCC** (Neotec Canonical Controls). If you license the Secure Controls
Framework, import it and make that canonical instead — nothing else changes.

The **Show Crosswalk** button on any control shows every framework it reaches.
**Conflicting Requirements** surfaces mapped controls whose severity or
automation posture disagrees, and names which obligation binds. Where frameworks
disagree, the stricter one is the one you actually have to meet.

## Gap assessment

Every in-scope control starts as `not_covered`. Evidence promotes it. That
ordering is deliberate: a control with no evidence is a gap, and the most common
failure of automated compliance tooling is to report absence of evidence as
absence of a problem.

Within a control, worst-wins: one failing evaluation fails the control however
many resources pass.

Two percentages are reported side by side and neither can mask the other:

- **Automated coverage** — share of in-scope controls any evidence touched
- **Compliance** — share of *assessed* controls that pass

100% compliance over 4% coverage means almost nothing was tested.

Findings older than their connector's cache TTL are excluded and reported as
stale, unless **Include Stale Findings** is ticked.

## Frameworks and licensing

| Framework | Controls | Notes |
|---|---:|---|
| NCC (canonical) | 62 | Original wording. Freely editable. |
| NCA ECC-2:2024 | 193 | Real control codes with domain and subdomain structure, 90 subcontrols |
| NIST CSF 2.0 | 106 | Subcategory codes and titles; US Government work |
| NIST SP 800-53 r5 | 58 | Identifiers; public domain |
| EU GDPR | 43 | Article numbers and titles; published legislation |
| ISO 27001:2022 | 40 | Annex A identifiers only |
| ISO 42001:2023 | 38 | Annex A identifiers only |
| ISO 22301:2019 | 36 | Clause numbers only |
| KSA PDPL | 31 | Articles, Implementing Regulations, Cross-Border rules, NDMO |
| PCI DSS 4.0 | 28 | Identifiers only |
| FedRAMP 20x KSI | 27 | Key Security Indicators |
| CIS v8 | 22 | Identifiers only |
| SOC 2 TSC | 12 | Identifiers only |
| NCA CCC | 4 | Cloud Cybersecurity Controls |
| SAMA CSF | 2 | Identifiers only |

702 controls, 1,121 crosswalk edges.

**No normative control text from any licensed standard is shipped.** Control
identifiers, clause numbers and issuer-published structure are references, which
is how every GRC tool cross-references. In particular, the NCA ECC-2 normative
Arabic text is not reproduced — only the control codes and the domain and
subdomain structure. Load titles and objectives from your own licensed copy.

`verify_tree.py` fails the build if standard-clause phrasing appears in the seed
data.

### Binding versus related mappings

Crosswalk edges carry a relationship. Only `equivalent`, `subset` and `superset`
carry coverage into a gap assessment. `related` edges are shown in the crosswalk
viewer and marked as not carrying coverage.

The distinction matters because 910 of the shipped edges are derived at family
level — canonical family to framework subdomain — rather than control to control.
Treating those as binding would let one passing governance check mark dozens of
target controls as covered, which is the coverage inflation this tool exists to
prevent. Narrow them to control level as you validate them, and change the
relationship to `equivalent` as you go.

## Data residency

**Residency Mode** in settings defaults to *On-Premise Only*, which blocks
non-local AI endpoints unless explicitly allow-listed. AI narrative generation
is off by default and, when enabled, targets a self-hosted Ollama endpoint —
there is no hosted-LLM path.

Raw connector attributes can be redacted on ingest by key name
(`password`, `token`, `iqama`, `national_id`, …).

## Scheduled jobs

| When | Job |
|---|---|
| Hourly | Queue due evidence sets, skipping any set with a run still in flight |
| Daily | Expire exceptions past their date |
| Daily | Notify owners of overdue risk, policy and vendor reviews |
| Daily | Snapshot KPI/KRI metrics |
| Daily | Purge artifacts past retention, **protecting evidence behind submitted assessments** |

## Reports

- **Control Coverage Matrix** — which controls can be evidenced automatically, and by which fetcher
- **Findings by Control** — open failures grouped by control, severity-ordered
- **Evidence Freshness** — last successful collection per fetcher against its TTL
- **Multi Framework Optimisation** — canonical controls ranked by risk closed per hour

## Documentation

`docs/NeoGRC-Implementor-Manual.docx` (and `.pdf`) is the 26-page
implementor manual: architecture, installation, roles, evidence collection, the
finding contract, crosswalk and gap assessment scoring, operations, licensing,
and how to add a connector or framework. Diagram sources are in `docs/figures/`.

## Demo data and testing

```bash
bench --site testsite execute neogrc.demo.dataset.install
bench --site testsite execute neogrc.demo.dataset.purge
```

Builds a fictional KSA entity, Wadi Marine Logistics, with a deliberately
imperfect programme: some controls pass, some fail, one is evidenced only by a
stale run, one carries an approved exception, and most have no evidence at all.
A demo where everything is green would not show whether the scoring works.

3 users, 3 policies, 4 vendors, 4 risks, 3 exceptions, 12 weeks of metrics and
9 findings across two batches. Everything is tagged `neogrc-demo`; `purge`
removes only tagged records and leaves seeded master data alone. Set **Block
Demo Data** in Settings on production sites.

Curl-ready payloads live in `neogrc/demo/fixtures/`:

| Fixture | Use |
|---|---|
| `valid_findings.json` | 8 contract-valid findings — pass, fail and inconclusive |
| `stale_findings.json` | One batch dated nine days back, for testing staleness |
| `invalid_findings.json` | 8 documents, each breaking exactly one contract rule |

`docs/NeoGRC-Test-Tour.docx` (and `.md`, `.pdf`) walks every subsystem in about
45 minutes, stating what you should see and — more usefully — what you should
not.

## Development

```bash
python3 verify_tree.py            # structural + licensing guard
python3 tests/test_offline_logic.py   # contract + rule engine, no bench needed
bench --site yoursite run-tests --app neogrc
```

`verify_tree.py` checks field-order consistency, link and table targets, patch
placement, hooks resolution, permission guards on every whitelisted method,
seed-data referential integrity, and that no Server Scripts are committed.

## Conventions

- No Server Scripts. All logic in versioned Python controllers.
- Patches under `[post_model_sync]` — every seeder writes to DocTypes the same
  migrate creates.
- Heavy operations are background jobs; nothing long-running holds a web worker.
- Every whitelisted endpoint is role-guarded.
- Idempotent `after_install` / `after_migrate`.

## Reference and attribution

Design informed by three open-source projects. No code was copied from any of
them; what was taken is the shape of the data contract and the collection
pipeline.

| Project | What informed this app |
|---|---|
| [claude-grc-engineering](https://github.com/GRCEngClub/claude-grc-engineering) (NeoGRC Engineering Club) | Finding schema v1.0.0, the risk/exception/vendor/policy/metric contracts, canonical-control crosswalk model, connector quality bar |
| [evidence-fetchers](https://github.com/paramify/evidence-fetchers) (Paramify) | Fetcher catalogue shape, evidence sets mapped to FedRAMP 20x KSIs, regex validation rules, timestamped run directories, structured exit codes |
| [NeoGRC Engineering Club directory](https://github.com/GRCEngClub/directory) | Specialisation and framework vocabulary |

Not affiliated with, or endorsed by, any of the above.
