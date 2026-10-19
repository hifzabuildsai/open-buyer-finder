# Open Buyer Finder

**Your product URL → market research, company evidence, prospects and outreach drafts.**

A local, open-source founder workbench. Research a product, propose six segments,
discover companies, compare competitors, enrich public business contacts and prepare
personalized outreach. Every run keeps sources, missing-data counts and usage.
Fourgate is the first example product.

MIT · Python 3.10+ · Zero Python runtime dependencies · Dashboard + CLI

## Open the workbench

From this repository folder:

```bash
python -m buyerfind studio
```

Open **http://127.0.0.1:8765** and choose **Try demo**. No API key or model is needed.
The demo's companies, people and competitors are fictional; it has no verified
emails and cannot enter external integrations.

The dashboard includes Overview, Companies, People, Competitors, Campaigns and
Signals, with search, segment filters, evidence dialogs and CSV downloads.

## Live research

Configure a model and search provider using [SETUP.md](docs/SETUP.md), then:

```bash
python -m buyerfind pipeline https://your-product.example --config examples/settings.local.json --out runs/first
```

Replace the example URL with your public product website. The pipeline reads
public HTML, extracts source-backed product facts, proposes segments and campaigns,
discovers candidates, and researches companies and competitors within your limits.

| Output | Target and behavior |
| --- | --- |
| Product profile | Sourced name, description and capabilities; proposed targeting hypotheses |
| Competitors | 15 direct/adjacent alternatives, official pricing, features and sourced complaints |
| Segments | Six buyer/pain hypotheses, inclusion/exclusion criteria and search queries |
| Companies | 100 fully enriched companies, deduplicated by normalized website host |
| Named prospects | Shortlist of 30–50 people, separate from shared/role business addresses |
| Contact enrichment | Public business addresses; optional Hunter discovery and verification |
| Campaigns | Three sequences per segment: two email variants and one LinkedIn draft, three steps each |
| Signals | Hiring/funding/launch/technology evidence with dated, stale or undated states |
| Exports | CSV, explicit HubSpot/Notion/Instantly handoff, separately approved email activation |

Targets are not guaranteed results. If only 73 companies qualify, the report
shows the shortfall. Missing facts remain missing. Live research never falls
back to demo data and never invents extra records to fill a quota.

## What counts as verified?

Factual extraction requires exact excerpts from retrieved pages or observed links.
Source support does not establish buying intent, purchasing authority, legal
entity verification or contact permission. AI fit remains an inference.

- **Source-supported company:** its name is supported by its fetched website.
- **Outreach-ready company:** supported identity, potential fit and an unsuppressed
  business email returned as valid by a provider within the last 30 days.
- **Fully enriched company:** outreach-ready plus sourced company LinkedIn and
  technology data. The full private technology stack is not implied.
- **Verified email:** a dated provider result. Accept-all, unknown, invalid,
  published-unverified and missing addresses never count as valid. A valid
  result is not a guarantee of inbox delivery.

The 100-company target uses the fully enriched count. Verification initially
prioritizes one contact per company to spread the credit budget. Extra contacts
remain explicitly unverified. All outreach still needs founder review.

## Free-first, optional providers

Defaults use local Ollama and SearXNG. These require installation, model downloads,
hardware and working search engines. Public-source mode does not verify email
deliverability. Optional adapters support Brave Search, an OpenAI-compatible chat
endpoint and Hunter. Provider usage may cost money; bring your own keys.

Limits bound searches, model calls, pages, domain lookups and email checks. Usage
counts failed requests too. Keys are never saved in research outputs. Retrieved
public text is sent to the selected model; select a local model for local processing.

## Saved research, monitoring and exports

Every run produces bundle.json, report.html and CSVs for companies, contacts,
competitors, sequences and triggers. Keep real data in the ignored runs/ folder.

```bash
python -m buyerfind export runs/first --kind companies --out runs/first/export.csv
python -m buyerfind monitor runs/first --config examples/settings.local.json
```

Monitor performs one refresh, records unique signals and preserves the previous
snapshot. An OS scheduler can invoke it repeatedly; no background job is installed.

CSV downloads are immediate. External writes require initial account setup,
credentials, a prepared local plan and approval of that exact plan:

```bash
python -m buyerfind sync-plan runs/first --destination hubspot --out runs/hubspot-plan.json
```

See [EXPORTS.md](docs/EXPORTS.md) for Notion schema setup, applying plans and
Instantly handoff. Importing leads does not send email. Separate activation checks
the exact recipients, copy, sender health, schedule, daily limit, reply stopping
and unsubscribe controls before starting provider-managed delivery/follow-ups.
LinkedIn output is drafts for manual use, not an automated messaging bot.
No live outreach was sent while building or testing this release.

## Current limits

- JavaScript-only, login-protected or robot-disallowed pages may be unreadable.
- Company identity and competitor pricing require fetched pages, not search snippets.
- Names, roles, fit and review interpretations need human assessment. Quote matching
  cannot prove semantic correctness. Missing reviews/private pricing stay unknown.
- Funding and hiring are signals, not proof of buying intent. Unsourced dates stay unknown.
- Discovery scans within provider budgets and retains at most three times the
  company target (bounded at 500); it can finish below target.
- Checkpoints preserve results. Automatic continuation after a process crash is
  not implemented; start a new run to repeat discovery.
- This is a single-user local app, not an internet-facing hosted CRM.

## Develop

```bash
python -m unittest discover -s tests -v
python -m pip install .
buyerfind --help
```

The package is not published to PyPI. [VALIDATION.md](VALIDATION.md) distinguishes
live checks from fixture tests and unverified integrations. Tests exercise a
100-company pipeline fixture, real localhost HTTP, source integrity, exports and
campaign activation boundaries. Fixture counts are not real prospects or sales.

Original GitHub/HN search/demo/review/feedback/summary commands still work;
[LEGACY_WORKFLOW.md](docs/LEGACY_WORKFLOW.md) records that v1 workflow.

[Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)
