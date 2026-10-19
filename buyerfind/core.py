"""Source collection and evidence integrity. No message-sending capability."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def now():
    return datetime.now(timezone.utc).isoformat()


class SourceError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an optional GitHub token to a redirect destination.
        return None


def get_json(url, *, token=None):
    host = urlsplit(url).hostname
    if urlsplit(url).scheme != "https" or host not in {"api.github.com", "hn.algolia.com"}:
        raise SourceError("Unsupported source endpoint")
    headers = {"User-Agent": "OpenBuyerFinder/0.1", "Accept": "application/json"}
    if token and host == "api.github.com":
        headers["Authorization"] = "Bearer " + token
    request = Request(url, headers=headers)
    try:
        with build_opener(NoRedirect).open(request, timeout=20) as response:
            raw = response.read(5_000_001)
        if len(raw) > 5_000_000:
            raise SourceError("Source response exceeds 5 MB")
        return json.loads(raw)
    except HTTPError as exc:
        raise SourceError(f"{host}: HTTP {exc.code}; check quota/access and retry later") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise SourceError(f"{host}: network or JSON response failure") from None


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "br", "div", "li"}:
            self.parts.append("\n")


def plain(value):
    parser = PlainText()
    parser.feed(value or "")
    return "".join(parser.parts).strip()


def load_profile(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("name", "description", "target_customer"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Profile needs a non-empty {key}")
    queries = data.get("queries")
    if not isinstance(queries, dict) or not any(queries.get(s) for s in ("github", "hn")):
        raise ValueError("Profile needs github and/or hn query lists")
    for source, values in queries.items():
        if source not in {"github", "hn"} or not isinstance(values, list) or len(values) > 3:
            raise ValueError("Use at most three queries per supported source")
        if any(not isinstance(q, str) or not q.strip() or len(q) > 180 for q in values):
            raise ValueError("Queries must be non-empty strings of at most 180 characters")
        if source == "github" and any(re.search(r"\bis:private\b", q, re.I) for q in values):
            raise ValueError("Only public GitHub research is supported")
    terms = data.get("relevance_terms", [])
    if not isinstance(terms, list) or any(not isinstance(t, str) or not t.strip() for t in terms):
        raise ValueError("relevance_terms must be a list of non-empty strings")
    return data


def evidence(source, item):
    if source == "github":
        if "pull_request" in item:
            return None
        url = item.get("html_url", "")
        if not re.fullmatch(r"https://github.com/[^/]+/[^/]+/issues/\d+", url):
            return None
        author = (item.get("user") or {}).get("login", "")
        title, body = item.get("title", ""), item.get("body") or ""
        published, updated = item.get("created_at"), item.get("updated_at")
        state = item.get("state", "unknown")
        profile = "https://github.com/" + quote(author, safe="")
    else:
        identifier = str(item.get("objectID", ""))
        if not identifier.isdigit():
            return None
        url = "https://news.ycombinator.com/item?id=" + identifier
        author = item.get("author", "")
        title = item.get("title") or item.get("story_title") or "HN comment"
        body = plain(item.get("comment_text") or item.get("story_text") or "")
        published, updated, state = item.get("created_at"), None, "unknown"
        profile = "https://news.ycombinator.com/user?id=" + quote(author, safe="")
    if not isinstance(title, str) or not isinstance(body, str) or not isinstance(author, str):
        return None
    return {"id": hashlib.sha256(url.encode()).hexdigest()[:16], "source": source,
            "url": url, "author": author, "profile_url": profile, "title": title,
            "text": body[:20000], "published_at": published, "updated_at": updated,
            "state": state, "decision": "unreviewed", "queries": []}


def collect(profile, since, *, limit=10, fetch=get_json, fixture=None):
    records, requests, warnings = {}, [], []
    timestamp = int(datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    for source, queries in profile["queries"].items():
        for index, query in enumerate(queries):
            if source == "github":
                bounded = f"{query} is:issue is:open is:public in:title,body created:>={since}"
                url = "https://api.github.com/search/issues?" + urlencode(
                    {"q": bounded, "per_page": 20, "sort": "created", "order": "desc"})
            else:
                url = "https://hn.algolia.com/api/v1/search_by_date?" + urlencode(
                    {"query": query, "hitsPerPage": 20, "numericFilters": f"created_at_i>={timestamp}"})
            request = {"source": source, "query": query, "url": url, "status": "ok"}
            requests.append(request)
            try:
                if fixture is not None:
                    payload = fixture[source][index]
                    if isinstance(payload, dict) and "fixture_error" in payload:
                        raise SourceError(payload["fixture_error"])
                elif source == "github":
                    payload = fetch(url, token=os.environ.get("GITHUB_TOKEN"))
                else:
                    payload = fetch(url)
                items = payload.get("items" if source == "github" else "hits")
                if not isinstance(items, list):
                    raise SourceError("Malformed source response")
                if payload.get("incomplete_results") or payload.get("exhaustiveNbHits") is False:
                    warnings.append(f"{source}: source reports incomplete search results")
                for item in items:
                    row = evidence(source, item)
                    if row is None:
                        continue
                    # Defense in depth: fixtures and unexpected API results obey the date boundary too.
                    date = row.get("published_at") or ""
                    if date[:10] < since or (row["source"] == "github" and row["state"] == "closed"):
                        continue
                    if row["url"] not in records:
                        records[row["url"]] = row
                    if query not in records[row["url"]]["queries"]:
                        records[row["url"]]["queries"].append(query)
                request["returned"] = len(items)
            except (SourceError, KeyError, IndexError, TypeError, AttributeError) as exc:
                # Do not present a failed source as a successful empty search.
                request["status"] = "error"
                warnings.append(f"{source}: {exc}" if isinstance(exc, SourceError)
                                else f"{source}: malformed source/fixture data")
    terms = profile.get("relevance_terms", [])
    for row in records.values():
        text = (row["title"] + " " + row["text"]).casefold()
        row["matched_terms"] = [term for term in terms if term.casefold() in text]
    rows = sorted(records.values(), key=lambda r: (-len(r["matched_terms"]), r["url"]))[:limit]
    return {"schema_version": 1, "generated_at": now(), "since": since,
            "mode": "synthetic_demo" if fixture is not None else "live_public_search",
            "profile": profile, "requests": requests, "warnings": warnings,
            "complete": not warnings, "retrieved_count": len(records), "items": rows,
            "reviews": [], "feedback": []}


def normalized(text):
    return " ".join(text.split())


def validate_reviews(run, reviews):
    if not isinstance(reviews, list):
        raise ValueError("Review file must be a JSON array")
    items = {row["id"]: row for row in run["items"]}
    seen = set()
    for review in reviews:
        if not isinstance(review, dict) or review.get("id") not in items or review["id"] in seen:
            raise ValueError("Every review needs a unique existing evidence id")
        seen.add(review["id"])
        if review.get("decision") not in {"potential_fit", "needs_context", "not_fit"}:
            raise ValueError("Invalid decision; confirmed-buyer judgments are not supported")
        if not isinstance(review.get("reason"), str) or not review["reason"].strip():
            raise ValueError("Review needs a reason")
        quote_text = review.get("evidence_quote")
        row = items[review["id"]]
        if (not isinstance(quote_text, str) or len(normalized(quote_text)) < 12
                or normalized(quote_text) not in normalized(row["title"] + "\n" + row["text"])):
            raise ValueError("Evidence quote must be an exact excerpt from the retrieved source")
        if review.get("source_url") != row["url"]:
            raise ValueError("Review source URL must match its evidence item")
        if not isinstance(review.get("unknowns"), list) or not review["unknowns"]:
            raise ValueError("List unresolved fit/authority/budget questions")
        if any(not isinstance(x, str) or not x.strip() for x in review["unknowns"]):
            raise ValueError("Unknowns must be non-empty strings")
        if not isinstance(review.get("next_question", ""), str):
            raise ValueError("next_question must be a string")
    return reviews


def atomic_text(path, text):
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".buyerfind-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_run(directory, run):
    from .report import render
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    atomic_text(path / "report.html", render(run))
    atomic_text(path / "run.json", json.dumps(run, ensure_ascii=False, indent=2) + "\n")


def load_run(directory):
    return json.loads((Path(directory) / "run.json").read_text(encoding="utf-8"))
