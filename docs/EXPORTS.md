# Export, review, and activate

Open Buyer Finder can export local tables, write selected records to HubSpot or
Notion, and hand a reviewed email sequence to Instantly. Importing a campaign
does **not** start it. A separate activation command enables real email delivery.
LinkedIn sequences remain drafts and manual tasks; there is no LinkedIn bot.

These adapters are implemented and covered by offline transport-boundary tests.
They have **not been validated against an authenticated live provider account**.
Provider subscriptions, API access, mailbox setup and usage charges are separate
from this project's MIT license. No provider account or messages were changed
during development.

## Download tables

```bash
python -m buyerfind export runs/my-product --kind companies --out runs/my-product/companies.csv
```

Kinds are `companies`, `contacts`, `sequences`, `competitors`, `triggers`,
`hubspot`, `notion`, and `instantly`. CSV strings neutralize spreadsheet formulas.
The `mode` column labels live versus fictional demo data; leave it unmapped when
using a provider's CSV import UI. Templates are local files, not API operations.
HubSpot and Instantly templates include only eligible contacts. Generic contacts
CSV retains unknown and invalid statuses for research and correction.

Synthetic runs can produce clearly labeled **local** CSV previews. Synthetic
runs cannot produce an external write plan or activation plan. Never import
the demo rows into a real outreach campaign.

## Prepare an external write

An export plan contains the exact records, request bodies, exclusions and a
SHA-256 `approval_hash`. It contains no API key. Review the plan's JSON before
applying it. The selected account is the account of the explicit provider token.

```bash
python -m buyerfind sync-plan runs/my-product --destination hubspot --out runs/hubspot-plan.json
```

Set the provider credential in your local environment using your normal secret
management method, then apply the reviewed plan:

```bash
python -m buyerfind sync-apply runs/hubspot-plan.json --approve EXACT_HASH_FROM_REVIEWED_PLAN
```

Changing any plan field invalidates the old hash. Plans expire after 24 hours.
`--approve` is an explicit local user action, not an approval inferred from a
research run, model response, website, or message saying “looks good.”

| Destination | Credential environment variable | Behavior |
| --- | --- | --- |
| HubSpot | `HUBSPOT_ACCESS_TOKEN` | Upserts contacts by email, one record at a time |
| Notion | `NOTION_API_KEY` | Creates company pages in a selected data source, skips domains already present |
| Instantly | `INSTANTLY_API_KEY` | Configures selected draft/paused campaign copy and imports eligible recipients |

HubSpot does not create company objects or guess that `domain` is a unique
upsert key. It does not label contacts customers, subscribe them to marketing,
or send email. Email is the contact upsert identity. HubSpot's documented email
upsert limitation applies: partial upserts by email are not supported. The plan
shows exactly the properties provided; existing CRM values need review.

Notion requires an explicit **data source ID**, access for the connected
integration, and these exact property names/types:

| Property | Notion type |
| --- | --- |
| Name | Title |
| Domain | Text (`rich_text`) |
| Website | URL |
| Fit reason | Text (`rich_text`) |
| Segment | Text (`rich_text`) |
| Evidence | Text (`rich_text`) |

Create `notion-options.json` with `{"data_source_id":"YOUR_DATA_SOURCE_ID"}`,
then pass `--options notion-options.json` to `sync-plan`. The adapter queries
Domain before creating a page and preserves existing pages unchanged. This is
an import with duplicate checks, not bidirectional synchronization. It pins
Notion API version `2025-09-03` and uses `/v1/data_sources/{id}/query`.

## Import an email campaign

First create an **empty draft campaign** in Instantly and configure its sender
accounts and sending schedule. Alternatively choose an empty paused campaign.
Choose one email sequence from one segment in your research bundle. Prepare an
options file with your actual IDs:

```json
{
  "campaign_id": "YOUR_CAMPAIGN_ID",
  "sequence_id": "YOUR_EMAIL_SEQUENCE_ID",
  "sender_name": "Your name",
  "daily_limit": 20,
  "suppress_emails": []
}
```

Optional `contact_ids` limits the selected contacts. Optional `company_ids`
limits a Notion import. Unknown options are rejected to prevent accidentally
writing keys into a plan. No credentials belong in options files.

```bash
python -m buyerfind sync-plan runs/my-product --destination instantly --options instantly-options.json --out runs/instantly-plan.json
```

Read each recipient's personalized `preview`, the selected campaign ID, the
email verification dates, exclusions, sequence delays and configuration changes.
Then apply the reviewed import hash using `sync-apply`.

The import:

1. Requires live data, source-supported company identity, potential product fit,
   a provider-verified `valid` email checked within 30 days, and no suppression.
2. Excludes duplicates, missing/invalid/accept-all/unverified/stale emails and
   contacts from another segment. Missing or unsupported personalization tokens
   fail before any provider calls.
3. Checks the campaign is draft/paused before every write, the existing lead set
   matches only this plan's completed receipts, and current Instantly workspace
   email/domain blocklists do not suppress any selected recipient.
4. Applies the selected sequence, a daily limit of 1–100, stop on reply,
   unsubscribe headers, text-only delivery and risky contacts disabled. Existing
   senders and schedule are preserved.
5. Creates recipients with workspace/campaign duplicate flags. The provider's
   returned IDs are recorded. A skipped or ambiguous result is not called success.

Sequence `day` values are absolute days since the first email. Instantly's
`delay` is the wait **after the current email before the next one**. Thus days
0, 3, 8 become delays 3, 5, 0. Text bodies are escaped and newlines become HTML
line breaks; subject merge fields use separate plain-text values. The plan
contains both readable recipient previews and the exact provider payloads.

## Start automated delivery explicitly

After import is complete, prepare a separate activation plan:

```bash
python -m buyerfind activation-plan runs/instantly-plan.json --out runs/activate-plan.json
```

This reads the provider; it does not send. It requires:

- Complete matching import receipts and exact imported recipient IDs/emails.
- The same sequence, timing, personalization, limits and sending controls.
- Active sender accounts, no unhealthy warmup status, passing sender domain
  vitals, and a configured sending schedule.
- No extra recipients, prior contact/reply/disposition, bounced/unsubscribed/
  skipped recipient state, new blocklist entry or conflicting provider verification.

The plan binds those current facts in a snapshot and expires in 30 minutes.
Read it and explicitly approve its hash to start the provider's delivery:

```bash
python -m buyerfind activation-apply runs/activate-plan.json --approve EXACT_ACTIVATION_HASH
```

Immediately before activation the adapter checks the current snapshot again.
After activation it reads back campaign status. A completed receipt means
**activation was observed**, not that email was delivered or someone replied.
Instantly executes the schedule, follow-ups, reply stopping and unsubscribe
behavior. Its dashboard remains the place to inspect sending and pause a
campaign. Delivery quality and actual conversions remain unmeasured until real
campaign results exist.

No provider transaction atomically locks the remote campaign between the last
read and activation. Do not concurrently edit or activate a campaign elsewhere
while applying a plan. The local lock prevents concurrent application of the
same plan; it is not a distributed lock across machines or provider users.

## Receipts and interrupted operations

A plan at `runs/plan.json` has receipts at `runs/plan.receipt.json`. Each attempted
write is marked `started` on disk **before** it is sent, and then `complete` or
`unknown`. Receipt updates use atomic replacement and file synchronization.
Completed operations are skipped when the same plan is reapplied. Unknown or
interrupted writes block automatic retries, including activation.

For an unknown result, inspect the exact record/campaign in the provider and
retain the plan and receipt as evidence. There is no automatic “force retry” or
reconciliation command in this version. Do not delete receipts to make an error
disappear. After a process crash, a `.lock` file may remain: resolve the receipt
and remote state before removing it. A new plan is appropriate only after the
previous outcome is understood. Preserving receipts is required for local
duplicate prevention.

Update suppression before preparing a plan. Python callers can additionally
pass `suppress_emails` to either apply function as a last-minute deny list; this
can only narrow the approved recipients. Instantly's live workspace blocklist
is rechecked automatically. Bundle edits after planning do not automatically
rewrite an approved plan—prepare a new one when the research changes.

Requests use fixed HTTPS provider hosts, bounded response sizes and timeouts,
no redirects, no automatic retries, and no persisted credentials. Recipient
reads are bounded to 1,000 contacts and suppression reads to 2,000 entries;
incomplete pagination blocks the operation rather than ignoring missing data.

## Python integration

```python
export_csv(bundle, kind="companies") -> str
prepare_export(bundle, destination, options=None, *, now=None) -> dict
save_export_plan(plan, path) -> pathlib.Path
apply_export(plan_path, approval, *, token=None, transport=None,
             now=None, suppress_emails=None) -> dict
prepare_activation(export_plan_path, *, token=None, transport=None, now=None) -> dict
apply_activation(plan_path, approval, *, token=None, transport=None,
                 now=None, suppress_emails=None) -> dict
```

`transport(method, url, headers, payload)` is the HTTP boundary and returns a
decoded JSON object. The default transport is real stdlib HTTPS. Tests supply
a stateful fake service only at that boundary. `now` controls time in tests.
`ExportError` subclasses `ValueError`. `save_export_plan` refuses overwriting an
existing plan.

## API sources

Implementation was checked against these primary sources on 2026-10-19:

- [HubSpot contacts, upsert and limitations](https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/contacts/guide)
- [Notion create page](https://developers.notion.com/reference/post-page)
- [Notion query data source](https://developers.notion.com/reference/query-a-data-source)
- [Instantly create lead](https://developer.instantly.ai/api-reference/lead/create-lead)
- [Instantly list leads](https://developer.instantly.ai/api-reference/lead/list-leads)
- [Instantly campaign schema](https://developer.instantly.ai/api-reference/campaign/get-campaign)
- [Instantly activate campaign](https://developer.instantly.ai/api-reference/campaign/activatestart-or-resume-a-campaign)
- [Instantly account vitals](https://developer.instantly.ai/api-reference/account/test-account-vitals)
- [Instantly workspace blocklist](https://developer.instantly.ai/api-reference/blocklistentry/list-block-list-entry)

Reading the documentation and passing fixture tests do not prove a live account
has the necessary permissions, subscription, schema or sending configuration.
