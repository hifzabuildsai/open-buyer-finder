# Local Research Studio

The studio is a local browser interface to the v2 URL research pipeline. It reads
the same saved bundles as the CLI. Provider settings and credentials stay in the
server process; there is no API-key field in the browser.

```bash
python -m buyerfind studio --config settings.json --workspace runs --port 8765
```

Open the exact `http://127.0.0.1:8765` address printed by the command. Keep that
terminal running. The server binds to `127.0.0.1` only; this is not a public hosted
application. Connecting through another hostname or a reverse proxy is unsupported.

## Use the workbench

1. Review the displayed providers and request limits. "Configured" does not mean
   a model or search service has been connection-tested. Start local services or
   supply the configured environment variables before running live research.
2. Enter a public product website and choose **Research website**. Provider usage
   follows your settings and can incur charges from your configured providers.
3. Watch the stage label and saved evidence appear. Only one run can be active at
   a time. Closing the browser does not cancel research while the Python process
   remains running. Stopping Python can interrupt it; completed checkpoints remain.
4. Inspect Overview, Companies, People, Competitors, Campaigns, and Signals.
   Search within a view, filter by segment, and open **View evidence** for dated
   excerpts and source links. People uses the bundle's named-person shortlist;
   company contact data can contain additional business/role contacts.
5. **Download CSV** downloads the current category. The People view exports all
   available contact records, so its CSV may contain more rows than the shortlist.
   No CRM is modified and no outreach is sent from the studio. Explicit provider
   handoffs are separate CLI operations; see the main README's export workflow.

**Explore fictional demo** creates a synthetic local pack without provider calls.
The yellow demo banner stays visible. Demonstration counts are not real prospects,
verified companies, or evidence that target coverage has been reached.

The headline target counts fully enriched companies, not all company names found.
An outreach-ready record needs potential fit, supported company identity, and a
non-suppressed contact with a provider-valid email checked recently. Fully enriched
records additionally have supported LinkedIn and technology-stack data. Separate
counts show discovered companies, source-supported and outreach-ready identities,
shortlisted people, competitors, and draft sequences. Missing data and partial
results remain visible.

## Storage and local boundaries

Only immediate, safe-named folders in the selected workspace can be loaded. A
research folder must contain `bundle.json` with schema version 2. The studio rejects
path traversal and symlinked folders/files; it does not expose an arbitrary local
file browser. Up to 100 recent folder names appear in the picker. Raw v1 `run.json`
reports remain available through the v1 CLI and are not listed in this view.

Browser mutations require both a per-process token and the exact local Origin.
All requests require an exact Host match to reduce DNS rebinding risk. POST bodies
are bounded, provider config cannot be changed from a request, and no cross-origin
access headers are set. A strict Content Security Policy blocks remote scripts
and embedding. Retrieved text is rendered with text nodes, source links allow only
HTTP(S), and no external font, image, or script service is used by the interface.

The studio is intended for one trusted local user. It is not a multi-user service
and does not protect files from other processes already running as that OS user.

## Tests

```bash
python -m unittest discover -s tests -p 'test_studio.py' -v
```

These tests use actual localhost HTTP requests and replace only the research and
export boundaries. They cover Host/Origin/token controls, oversized and malformed
requests, path/symlink restrictions, credential redaction, synthetic runs, observed
progress, concurrency limits, and download semantics. They do not establish live
provider connectivity or the quality of collected company information.
