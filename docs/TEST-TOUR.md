# NeoGRC Test Tour

A guided pass through every subsystem, using the demo dataset. About 45 minutes
end to end. Each step states what you should see, and several state what you
should **not** see — those are the ones worth doing carefully, because a control
that silently passes when it should fail is the failure mode that matters here.

Run this on a scratch site. Never on production.

---

## 0. Setup

```bash
bench --site testsite install-app neogrc
bench --site testsite migrate
bench --site testsite execute neogrc.demo.dataset.install
```

The installer prints a JSON summary. Expect 3 users, 3 policies, 4 vendors,
4 risks, 3 exceptions, 12 metrics, and 9 findings ingested across two batches.

Every record it creates is tagged `neogrc-demo`. To undo everything:

```bash
bench --site testsite execute neogrc.demo.dataset.purge
```

**The demo company is Wadi Marine Logistics**, a fictional KSA shipping and
warehousing operation. Its programme is deliberately imperfect: some controls
pass, some fail, one is evidenced only by a stale run, one has an approved
exception, and most have no evidence at all. A demo where everything is green
would not tell you whether the scoring works.

---

## 1. Install integrity

**Do:** open the NeoGRC Engine workspace.

**Expect:** 15 frameworks, 702 controls, 1,121 crosswalk edges, 9 connectors,
38 fetchers, 5 evidence sets.

**Do:** open **NeoGRC Framework** and sort by Canonical.

**Expect:** exactly one canonical framework, `NCC`. If two are flagged canonical,
the crosswalk pivot is ambiguous and everything downstream is unreliable.

**Do:** open **NeoGRC Control**, filter framework = `NCA-ECC-2`.

**Expect:** 193 controls, 90 of them flagged Is Subcontrol, with Domain and
Subdomain populated. Check that `2-8-1` sits under subdomain "Cryptography" and
`2-7-1` under "Data and Information Protection" — those two were transposed in
v0.1.0 and the fix is what this check confirms.

**Do:** run `bench --site testsite migrate` a second time.

**Expect:** counts unchanged. Seeders are idempotent; a second migrate that
creates duplicates means the uniqueness key is wrong.

---

## 2. The finding contract

This is the gate everything else depends on, so test it before anything else.

### 2a. A valid batch is accepted

```bash
curl -X POST https://testsite/api/method/neogrc.api.ingest_findings \
  -H "Authorization: token <key>:<secret>" \
  -H "Content-Type: application/json" \
  --data @apps/neogrc/neogrc/demo/fixtures/valid_findings.json
```

**Expect:** 8 findings created. Re-run the same command.

**Expect:** 8 findings **updated**, not 16 created. Ingestion is idempotent on
`run_id` + `source` + `resource_id` + `resource_type`.

### 2b. Dry run writes nothing

Add `"dry_run": 1` to the payload and post it with a changed `run_id`.

**Expect:** a validation report and **no new records**.

### 2c. An invalid batch is rejected whole

```bash
curl -X POST https://testsite/api/method/neogrc.api.ingest_findings \
  -H "Authorization: token <key>:<secret>" \
  -H "Content-Type: application/json" \
  --data @apps/neogrc/neogrc/demo/fixtures/invalid_findings.json
```

**Expect:** rejection with eight indexed errors, and **zero records written**.
Each document breaks exactly one rule:

| Index | Expected error |
|---:|---|
| 0 | `fail` without a message |
| 1 | `fail` without a severity |
| 2 | `inconclusive` without a message |
| 3 | status `green` is not in the enum |
| 4 | no evaluations |
| 5 | schema major version 2 unsupported |
| 6 | negative `effort_hours` |
| 7 | unparseable `collected_at` |

**The critical assertion is that nothing was written.** Document 0 is invalid,
but documents 1–7 would each have been individually rejected too — if any
records appear, whole-batch validation is broken, and a half-ingested run will
later report its missing half as uncovered with nobody noticing.

### 2d. Permission guard

Repeat 2a as a user holding only **NeoGRC Auditor**.

**Expect:** `PermissionError`. Auditors read; they do not write findings.

---

## 3. Rollups and severity

**Do:** open finding for resource `wadi-prod-manifests`.

**Expect:** Rollup Status `fail`, Worst Severity `high`. Two failing evaluations,
CRY-01 at high and DAT-01 at medium; the worse of the two wins.

**Do:** open `wadi-org-trail`.

**Expect:** Rollup Status `inconclusive`, not `pass`. LOG-01 passed but LOG-02
came back inconclusive with an AccessDenied message. Inconclusive outranks pass —
the tool tried and could not determine, which is not the same as fine.

**Do:** open `arn:aws:kms:...:key/abcd`.

**Expect:** CRY-04 is `not_applicable` with a message, and does not drag the
rollup down.

**Do:** open `wadi-prod-invoices`.

**Expect:** Rollup `pass`, Worst Severity blank or `info`. A passing evaluation
carrying a high severity must **not** inflate the finding's severity — severity
describes impact if failing, not current state.

---

## 4. Crosswalk

**Do:** open control `NCC::CRY-01` and click **Show Crosswalk**.

**Expect:** mappings into several frameworks, each row marked whether it carries
coverage. Binding rows (`equivalent`, `subset`, `superset`) do; `related` rows do
not and are labelled accordingly.

**Do:** open control `NCC::GOV-01` and click **Show Crosswalk**.

**Expect:** a long list, mostly `related`. These are the family-level derivations
into NCA ECC-2 and NIST CSF 2.0 — 910 of the 1,121 shipped edges. They are shown
because they are a useful starting point for a consultant, and excluded from
scoring because nobody has checked them at control level yet.

**Do:** click **Conflicting Requirements** on a control mapped into three or more
frameworks.

**Expect:** rows where severity or automation posture disagrees, with the binding
(stricter) obligation named.

**Do:** open **NeoGRC Settings** and change Canonical Framework to something
else, then re-run Show Crosswalk on CRY-01.

**Expect:** different results, and no error. The pivot is configurable by design.
**Change it back to `NCC` before continuing.**

---

## 5. Gap assessment

This is where the scoring philosophy is visible, so it is the most important
section of the tour.

### 5a. Single framework

**Do:** create a **NeoGRC Gap Assessment**, title "Demo — NCC baseline", As Of
now, add framework `NCC`. Click **Run Assessment**.

**Expect** on the scorecard:

- **Coverage well under 20%.** Nine findings cannot evidence 62 canonical
  controls. If coverage looks high, evidence is being counted for controls it
  never touched.
- **Compliance noticeably higher than coverage.** Of the controls actually
  assessed, most pass.
- Controls Not Covered is the largest bucket.

**The key check:** these two numbers must be reported separately and neither may
be presented as the headline alone. A blended figure would let this dataset look
respectable when in fact almost nothing was tested.

### 5b. Worst-wins within a control

**Do:** find CRY-01 in the results table.

**Expect:** `fail`. Three resources reported CRY-01 — `wadi-prod-manifests`
failed, `wadi-prod-invoices` and `wadi-erp-prod` passed. Two out of three passing
does not pass the control, because the failing bucket is the one that appears in
the incident report.

### 5c. Staleness

**Do:** find NET-02 and CHG-01 in the results.

**Expect:** reported as **stale**, not as pass. Their only evidence is the
`k8s-inspector` batch from nine days ago, and that connector's cache TTL is 24
hours. Evidence has a shelf life.

**Do:** create a second assessment with **Include Stale Findings** ticked.

**Expect:** NET-02 and CHG-01 now score as pass, and coverage rises slightly.
Both behaviours are correct; the tick makes the choice explicit.

### 5d. Multi-framework

**Do:** create an assessment scoped to `NCC`, `NCA-ECC-2` and `ISO-27001-2022`.

**Expect:** coverage drops sharply. ECC-2 alone contributes 193 controls, almost
all of them reached only by `related` edges that do not carry coverage.

**This is the correct result and worth understanding before a client sees it.**
Those ECC mappings are family-level derivations. Until a consultant narrows them
to control level and marks them `equivalent`, they must not answer a regulator's
control. Low coverage here is the tool being honest, not the tool being broken.

### 5e. Submission protects evidence

**Do:** submit the first assessment. Then check **NeoGRC Evidence Artifact**.

**Expect:** artifacts behind the submitted assessment are exempt from the
retention purge. Submitting is what makes evidence permanent.

---

## 6. Declarative query fetchers

**Do:** open **NeoGRC Evidence Set** → `ES-ERP-QUERY` → **Run Now**.

**Expect:** a run touching 7 enabled fetchers (2 of the 9 ship disabled because
their correct filters depend on site conventions — read their Instructions).

Then check individual results:

| Fetcher | Expect on the demo dataset |
|---|---|
| `EVD-Q-POLICY-REVIEW` | **fail** — DEMO-POL-002 is 435 days past review |
| `EVD-Q-EXCEPTION-EXPIRY` | **fail** — DEMO-EXC-002 expired 45 days ago, still approved |
| `EVD-Q-VENDOR-DPA` | **fail** — Northline Payroll processes personal data with no DPA |
| `EVD-Q-RISK-REVIEW` | **fail** — DEMO-RSK-004 is past its review date |
| `EVD-Q-API-KEYS` | depends on your site |
| `EVD-Q-STALE-SESSIONS` | likely **fail** — the three demo users are dormant by design |

**Do:** open a failing finding from one of these.

**Expect:** one resource per failing row, named. "Four vendors" is a status;
naming the four is a task list.

### 6a. Save-time validation

**Do:** edit `EVD-Q-POLICY-REVIEW`, change Filters to
`{"nonexistent_field": 1}`, and save.

**Expect:** rejection at save, naming the field and the DocType. Not at run time.
A query that fails at 2am inside a scheduled run surfaces as an unexplained
collection fault; a query that fails at save surfaces in front of the person who
made the mistake.

**Do:** try Filters `{"api_secret": ["is", "set"]}` on DocType `User`.

**Expect:** refusal — credential-shaped fields cannot be recorded as evidence.
Artifacts are hashed, attached and retained, and a secret must not become a
permanent audit record.

**Do:** try malformed JSON, e.g. `{"enabled": 1,}`.

**Expect:** rejection naming the parse error.

### 6b. Conditions

Change `EVD-Q-POLICY-REVIEW`'s condition to `COUNT_LTE_THRESHOLD` with threshold
`5` and re-run.

**Expect:** now passes. One overdue policy is within a tolerance of five.

---

## 7. Internal Python fetchers

**Do:** run `ES-ERP-BASELINE`.

**Expect:** 11 fetchers, artifacts with SHA-256 hashes attached to the run, and
findings emitted. No credentials, no outbound network.

**Do:** open any artifact and check the hash is populated and the file attached.

**Do:** open a fetcher with no validation rules (create one temporarily) and run
it.

**Expect:** `inconclusive`, never `pass`. Collecting a file is not the same as
demonstrating a control.

---

## 8. Preflight and exit codes

**Do:** enable the `aws-inspector` connector without setting a Script Root.
Click **Preflight Check**.

**Expect:** exit code 5, one clear message about the missing script root — not
eleven separate fetcher failures.

**Do:** set Script Root to a directory that exists but contains no scripts, and
run one fetcher.

**Expect:** exit 5, "script not found", and **no findings emitted**. A failed run
must never emit a pass.

**Do:** set a fetcher handler to `../../etc/passwd`.

**Expect:** refusal at save or at resolution. Handlers cannot escape the script
root. Also try an absolute path and a symlink pointing outside the root.

**Disable `aws-inspector` again before continuing.**

---

## 9. Programme records

### 9a. Risk scoring

**Do:** open `DEMO-RSK-001`.

**Expect:** inherent 4×5 = 20, band **Critical**. Residual 2×5 = 10, band
**Medium**. Bands recompute on save.

**Do:** open `DEMO-RSK-002` (residual 4×4 = 16, High) and change Treatment to
`accept` without linking an exception.

**Expect:** blocked. A critical or high residual risk cannot be accepted without
a documented exception. **Undo this change.**

### 9b. Exception segregation of duties

**Do:** open `DEMO-EXC-003` (status `requested`, raised by Sara). As Sara, try to
approve it.

**Expect:** blocked. The requester cannot be the approver. This is deliberately
not configurable — an exception register where the requester signs their own
approval documents nothing, and it is the first thing a competent auditor tests.

**Do:** approve it as Noor.

**Expect:** accepted.

**Do:** create an exception with no expiry date.

**Expect:** blocked. An exception without an end date is a permanent policy
change wearing a temporary label.

### 9c. Vendor PDPL gate

**Do:** open `DEMO-VEN-002` (Northline Payroll), confirm Processes Personal Data
is ticked and DPA Reference is empty. Save.

**Expect:** a warning or block on the missing DPA. Under PDPL Article 20 a
processor without a contract is the controller's breach, not the processor's.

**Do:** check Next Review At against tier. Critical tier should carry a shorter
cadence than low tier.

### 9d. Policy acknowledgment

**Do:** open `DEMO-POL-003` (draft) and change status to `approved`.

**Expect:** a queued job issuing one **NeoGRC Policy Acknowledgment** row per
active Employee, or per enabled desk user if ERPNext is not installed.

**Do:** as any user, mark **another** person's row acknowledged.

**Expect:** blocked. Only the subject may sign their own row. An acknowledgment
register signed off in a bulk edit is evidence of nothing.

**Do:** bump the policy version to `0.4` and re-approve.

**Expect:** a fresh round of rows against the new version. Sign-off on v0.3 does
not carry forward to v0.4.

---

## 10. Reports

| Report | Expect |
|---|---|
| **Control Coverage Matrix** | Which of the 62 NCC controls a fetcher can evidence, and which fetcher. Most will be blank — that is the honest picture. |
| **Findings by Control** | NET-01 at the top (critical, 0.0.0.0/0 on port 22), then the high-severity failures. |
| **Evidence Freshness** | `k8s-inspector` shown as stale against its TTL; `erpnext-internal` current. |
| **Multi Framework Optimisation** | CRY-01 ranked high — 1 hour of effort, auto-fixable, and it reaches several frameworks. |

**Do:** on the optimisation report, check that a control reaching six frameworks
at four hours outranks one reaching a single framework at the same cost.

---

## 11. Scheduled jobs

```bash
bench --site testsite execute neogrc.setup.scheduler.expire_exceptions
```

**Expect:** `DEMO-EXC-002` moves to `expired`. It was approved with an expiry 45
days in the past.

```bash
bench --site testsite execute neogrc.setup.scheduler.flag_overdue_reviews
```

**Expect:** notifications for DEMO-POL-002, DEMO-RSK-004, and the overdue
vendors.

```bash
bench --site testsite execute neogrc.setup.scheduler.snapshot_metrics
```

**Expect:** a new **NeoGRC Metric** row. The demo seeds twelve weeks of
`automation.coverage_pct` so a trend is already visible.

```bash
bench --site testsite execute neogrc.setup.scheduler.purge_expired_artifacts
```

**Expect:** artifacts behind the submitted assessment from step 5e survive.

**Do:** run `ES-ERP-QUERY` twice in quick succession.

**Expect:** the second is skipped while the first is in flight. A slow collection
must not stack.

---

## 12. Residency and redaction

**Do:** open **NeoGRC Settings**. Confirm Residency Mode is `On-Premise Only`.
Set AI Endpoint to `https://api.openai.com/v1` and save.

**Expect:** blocked. In on-premise mode, non-local AI endpoints are refused
unless explicitly allow-listed. This is enforced at validation, not left to
operator discipline.

**Do:** set it to `http://localhost:11434`.

**Expect:** accepted.

**Do:** post a finding whose `resource.attributes` contains a key named
`password` or `token`.

**Expect:** the value stripped on write. Check the stored record, not the
response.

---

## 13. Role separation

Log in as each demo user and confirm:

| Role | Can | Cannot |
|---|---|---|
| **NeoGRC Auditor** (Khalid) | Read controls, findings, evidence, artifacts, assessments | Create or edit anything; ingest findings |
| **NeoGRC Engineer** (Sara) | Build connectors and fetchers, run collection and assessments | Delete audit records; approve her own exception |
| **NeoGRC Manager** (Noor) | Everything, including delete and exception approval | Approve an exception he raised himself |

The demo users ship **disabled**. Enable one and set a password to test a role,
then disable it again.

---

## 14. Coexistence with your other GRC apps

If `alphax_grc` or `grc_suite` are installed on the same bench:

```bash
bench --site testsite install-app alphax_grc
bench --site testsite migrate
```

**Expect:** clean migrate. Every NeoGRC DocType is prefixed `NeoGRC `, and the
roles are `NeoGRC Manager` / `Engineer` / `Auditor`. Before the rename these
collided on `GRC Control`, `GRC Framework`, `GRC Policy`, `GRC Vendor`,
`GRC Exception`, `GRC Risk`, `GRC Manager` and `GRC Auditor` — DocType and Role
names are globally unique per site, so installing both would have broken migrate
or silently shared schema.

---

## 15. Teardown

```bash
bench --site testsite execute neogrc.demo.dataset.purge
```

**Expect:** demo records removed; seeded frameworks, controls, crosswalks,
connectors and fetchers untouched. Re-check the step 1 counts — they should be
identical.

To remove the demo users too:

```bash
bench --site testsite execute neogrc.demo.dataset.purge --kwargs "{'delete_users': 1}"
```

---

## Quick reference — what should fail

If you only have ten minutes, test these. Each is a case where a silent pass
would be worse than an obvious crash.

1. Invalid batch → **whole batch rejected, nothing written** (2c)
2. Fetcher with no validation rules → **inconclusive, never pass** (7)
3. Failed collection run → **no findings emitted** (8)
4. One failing resource among many → **control fails** (5b)
5. Evidence past TTL → **stale, not pass** (5c)
6. Requester approving their own exception → **blocked** (9b)
7. Signing someone else's policy acknowledgment → **blocked** (9d)
8. Query filter naming a credential field → **refused at save** (6a)
9. Handler path escaping the script root → **refused** (8)
10. Coverage and compliance → **reported separately, never blended** (5a)
