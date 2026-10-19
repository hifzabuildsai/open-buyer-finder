# v0.2 validation — 19 October 2026

This is a tested local prototype. The checks below do not establish that it can
find 100 real, relevant, fully enriched companies for an arbitrary product.

## Executed on the integrated code

- `python -m unittest discover -s tests -v`: **54 tests passed**, exit 0, on
  Python 3.12.14. The final run took 7.978 seconds.
- The real pipeline ran against fictional provider responses for 100 distinct
  company domains, a 50-person shortlist, six segments and 18 sequences. A smaller
  fixture exercised every completion criterion. These are engineering fixtures,
  not actual companies, verified inboxes, customers or conversion results.
- Checks covered exact source/value matching, fabricated quotations, missing
  models, partial checkpoints, email status/freshness, budgets, private-network
  rejection, safe HTML/CSV output and deduplicated trigger refreshes.
- Provider tests used actual localhost HTTP for Ollama/SearXNG-shaped responses.
  Compatible chat, Brave, Hunter and CRM boundaries used controlled fake responses.
  These tests exercise request/response handling; they do not validate live services.
- Export tests exercised HubSpot upserts, Notion duplicate handling, Instantly
  import and separate activation, exact-plan approval, current recipient/copy
  checks, suppression, sender checks, expiry and uncertain-write receipts.
- Real headless Chromium exercised all six dashboard tabs, evidence dialogs,
  filtering, CSV download, polling, hostile text/URLs, and 1440-pixel desktop and
  390-pixel mobile layouts. No page/console errors or document overflow occurred.
  Desktop and mobile screenshots were visually inspected.
- A v0.2.0 wheel was built with setuptools, installed without dependencies in a
  fresh virtual environment, and exercised from outside the source checkout.
  The installed CLI created its demo. The installed HTTP server served all three
  UI assets, ran the demo through its API and exported a labelled synthetic CSV.
  The demo contains 12 fictional companies, 15 fictional competitors, six segments
  and 18 sequences, with zero provider-verified emails.
- `git diff --check` and `git diff --cached --check` passed.

## Live Fourgate boundary check

The public homepage, pricing and integrations pages at
https://fourgate-site.vercel.app were fetched successfully: 12,157, 4,348 and
5,360 extracted text characters respectively. The environment required the
explicit trusted-proxy setting documented in SETUP.md.

The actual CLI pipeline then attempted the configured local model. No Ollama
service was available at 127.0.0.1:11434. The run exited 2, retained the three
sources, recorded `status: blocked`, and reported zero companies and the actual
target shortfalls. It did not substitute synthetic results.

## Not yet demonstrated

- A full live run using an installed model, working search engines and enrichment.
- Real relevance/accuracy, 100 enriched-company coverage, response or conversion rates.
- Live Hunter/Brave/model credentials, HubSpot/Notion account writes, Instantly
  import, or email delivery. No external messages were sent during development.
- Docker Compose startup and model download on the user's machine.
- Windows/macOS execution, Python 3.10/3.13 execution, hosted GitHub Actions,
  production deployment or publication to GitHub/PyPI.

Review source claims and recipient suitability before any campaign. Provider
validation is time-limited and does not guarantee deliverability. Keep live data,
credentials and campaign receipts out of the public repository.
