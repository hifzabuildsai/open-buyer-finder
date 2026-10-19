# Local setup

## Try without accounts

Python 3.10+ is sufficient. From the unpacked folder run `python -m buyerfind studio`.
On Windows, `py` may be your Python launcher. Open the exact printed
http://127.0.0.1:8765 address and choose Try demo. The fictional demo runs offline.

## Local live model and search

Install [Ollama](https://docs.ollama.com/) and a model that reliably returns JSON.
The example uses [qwen3:8b](https://ollama.com/library/qwen3:8b), an illustrative
configuration, not a benchmarked recommendation. Memory and speed depend on your machine.
Run `ollama pull qwen3:8b` to download it.

Run [SearXNG](https://docs.searxng.org/admin/installation-docker.html) locally with
JSON search enabled. Optional Docker Compose sets up both services on loopback:

```bash
docker compose -f compose.local.yml up -d
docker compose -f compose.local.yml exec ollama ollama pull qwen3:8b
python -m buyerfind studio --config examples/settings.local.json
```

Container/model downloads use disk and bandwidth. Images use upstream latest tags
for local development; pin reviewed digests for a reproducible deployment. This
compose recipe was not executed in the build environment. Search may still hit
upstream engine limits. GPU configuration follows Ollama's official instructions.

## Optional providers

Copy examples/settings.providers.json to settings.private.json and select your
model endpoint. The compatible adapter expects a base URL ending in /v1 with
/chat/completions supporting JSON object responses. Local gateways may be
unauthenticated; remote endpoints require HTTPS.

Set only the needed environment variables in the terminal that starts the app:

| Variable | Purpose |
| --- | --- |
| BRAVE_SEARCH_API_KEY | Brave web search |
| BUYERFIND_LLM_KEY | Selected compatible model endpoint |
| HUNTER_API_KEY | Hunter discovery and email verification |
| HUBSPOT_ACCESS_TOKEN | Explicit HubSpot export |
| NOTION_API_KEY | Explicit Notion export |
| INSTANTLY_API_KEY | Explicit Instantly import/activation |

Never put secret values in config JSON, bundles or Git. The app does not load .env
automatically or accept keys from the browser. Dashboard readiness shows config
and key presence, not a successful connection test.

## Bound usage

Defaults: 60 search calls, 180 model calls, 450 public pages, 100 Hunter domain
searches and 100 email checks. These are upper bounds, not a price estimate.
Check current provider pricing before enabling paid adapters. Reduce budgets
for a small trial. Zero network/model budgets block that work visibly.

Optional company_urls seeds known company websites. They still receive source,
fit and contact checks. The model selects among all six segments.

## Network and data boundaries

Public requests normally resolve and pin public IP addresses. Private/metadata
addresses, URL credentials, non-web schemes and unusual ports are rejected.
API credentials never follow redirects. The model cannot execute tools or shell
commands; web content is untrusted evidence.

If an administrator-controlled environment requires an egress proxy and offers
no local DNS, set BUYERFIND_TRUST_PROXY=1 alongside HTTP_PROXY/HTTPS_PROXY. The
trusted proxy MUST enforce public-only DNS/destination rules; this mode delegates
DNS enforcement to that proxy rather than pinning locally. Never use an untrusted
proxy. Literal private targets remain blocked. Local model/search bypass it.

Keep the studio on loopback. Host/Origin/request-token checks do not replace
internet-facing authentication. Real bundles contain prospect data; keep them private.

## References

- [Ollama chat API](https://docs.ollama.com/api/chat)
- [SearXNG search API](https://docs.searxng.org/dev/search_api.html)
- [Brave Search API](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)
- [Hunter API](https://hunter.io/api-documentation/v2)
- [Export setup and API sources](EXPORTS.md)
