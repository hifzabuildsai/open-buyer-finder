# URL-to-outreach workflow contract

The v2 workflow extends the v1 issue-research CLI. It runs locally, with explicit
provider settings and bounded usage. A live run never substitutes fictional data.
Demo mode is isolated and cannot be exported to external services or launched.

## Public Python interface

`buyerfind.market.run_pipeline(url, config, output, progress=None) -> bundle`
is synchronous and writes checkpoints to a NEW output directory. `progress`
receives the current bundle after each stage. `load_config(path=None)` validates
settings; `demo_bundle()` returns an explicitly fictional bundle;
`save_bundle(output, bundle)` writes bundle.json and portable outputs.

`buyerfind.providers.Providers(config)` implements page(url), search(query),
ask(task, data), enrich(domain), verify(email). The pipeline accepts an optional
keyword-only `providers` instance for boundary tests. HTTP, models and billing
are genuine external boundaries. Model output is untrusted.

## Bundle schema (schema_version 2)

- id, mode (`live` or `synthetic_demo`), status (`running`, `partial`, `complete`,
  `blocked`, `failed`), stage, created_at, updated_at, product_url
- targets: companies=100, competitors=15, segments=6, people_min=30,
  people_max=50, sequences_per_segment=3
- product: name, description, capabilities[], exclusions[], source_urls[]
- segments[]: id, name, buyer_role, pain, include[], exclude[], queries[], angle
- companies[]: id, name, domain, website, segment_id, fit_status
  (`potential_fit`, `needs_context`, `not_fit`), fit_reason, evidence[],
  identity_status (`source_supported` or `unknown`), industry, location,
  linkedin, tech_stack[], contacts[], triggers[], checked_at
- contacts[] within each company: id, name, role, linkedin, email,
  email_status (`valid`, `accept_all`, `unknown`, `invalid`, `published_unverified`,
  `missing`), email_provider, email_checked_at, evidence[], do_not_contact=false
- people_shortlist[]: deduplicated named-contact IDs, capped at people_max;
  this is a prospect shortlist, not a count of acquired customers.
- evidence[] entries: source_url, quote, checked_at. Quotes must occur in retrieved
  source text/links. Inferred fit is not a verified fact or purchase probability.
- tech_stack[]: name, evidence[], status (`source_supported` or `unknown`)
- triggers[]: id, company_id, kind (`hiring`, `funding`, `launch`, `technology`),
  summary, event_date (ISO date or null), evidence[], status (`dated_signal`,
  `undated_signal`, `stale_signal`). A signal is not proof of buying intent.
- competitors[]: id, name, website, relationship (`direct`, `adjacent`), reason,
  pricing (string or null), features[], review_gaps[] {complaint, marketing_angle,
  evidence[], review_date}, evidence[], checked_at
- sequences[]: id, segment_id, name, channel (`email` or `linkedin`),
  status=`draft`, steps[] {day, subject, body}. Three sequences per segment;
  three steps per sequence. Permitted placeholders: {{first_name}},
  {{company_name}}, {{product_name}}, {{fit_reason}}, {{sender_name}}.
  Generated copy has no measured conversion claim and needs founder review.
- sources[]: url, title, text, links[], fetched_at, kind (`page` or `search`)
- warnings[] strings; usage{} counters; summary{} computed by summarise(bundle)
- summary: companies, source_supported_companies, outreach_ready_companies,
  enriched_companies, people, verified_emails, competitors, segments, sequences,
  target_met, shortfalls[] strings. `target_met` is never true for synthetic data.

An outreach-ready company needs potential_fit, supported identity and at least
one non-suppressed contact with an exact provider-verified valid email checked
within 30 days. Fully enriched additionally requires supported LinkedIn and
tech-stack data. Missing values stay missing. Source counts do not count buyers.

## User interfaces

`buyerfind pipeline URL --config settings.json --out runs/name` runs the pipeline.
`buyerfind studio --config settings.json --workspace runs --port 8765` serves the
local dashboard on 127.0.0.1 only. No keys are accepted from browser requests.
`buyerfind pack-demo --out runs/demo-v2` writes the synthetic full-workflow demo.
`buyerfind monitor RUN --config settings.json` refreshes trigger research once;
an OS scheduler may invoke it repeatedly. No background task is installed here.

## Exports and sending

`buyerfind.exports.export_csv(bundle, kind='companies') -> str` supports
companies, contacts, sequences, competitors, triggers, hubspot, notion, instantly.
`prepare_export(bundle, destination, options) -> plan` builds a reviewable local
request plan, never sends. `apply_export(plan_path, approval, ...)` applies only
an exact approved plan with local operation receipts, using explicit credentials.
HubSpot/Notion sync is separate from email delivery. Instantly handoff uses an
explicitly selected DRAFT or PAUSED campaign and never activates it implicitly.
Activation is a separate explicit operation binding campaign, recipients and copy.
LinkedIn output is draft text and a task queue; there is no LinkedIn sending bot.

No external messages, account mutations or live exports are authorized by the
development request. Live provider/account tests need configured test credentials;
fixture tests must not be described as live integration validation.
