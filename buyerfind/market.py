"""URL-to-outreach orchestration. Evidence and usage survive partial failures."""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .core import atomic_text, now
from .net import FetchError, validate_url
from .providers import BudgetExceeded, Providers

DEFAULT_CONFIG = {
    "model": {"provider": "ollama", "endpoint": "http://127.0.0.1:11434", "model": "qwen3:8b",
              "api_key_env": "BUYERFIND_LLM_KEY"},
    "search": {"provider": "searxng", "endpoint": "http://127.0.0.1:8080", "api_key_env": "BRAVE_SEARCH_API_KEY"},
    "enrichment": {"provider": "public", "api_key_env": "HUNTER_API_KEY"},
    "limits": {"companies": 100, "contacts": 50, "competitors": 15, "search_requests": 60,
               "model_calls": 180, "pages": 450, "domain_searches": 100, "email_verifications": 100},
    "trigger_window_days": 90, "company_urls": []}
EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def load_config(path=None):
    data = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    return validate_config(data)


def validate_config(data):
    if not isinstance(data, dict) or any(k not in DEFAULT_CONFIG for k in data):
        raise ValueError("Unknown configuration field")
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in data.items():
        if isinstance(cfg[key], dict):
            if not isinstance(value, dict) or any(k not in cfg[key] for k in value):
                raise ValueError(f"Unknown {key} setting")
            cfg[key].update(value)
        else:
            cfg[key] = value
    for key, allowed in {"model": {"ollama", "openai_compatible"}, "search": {"brave", "searxng"},
                         "enrichment": {"public", "hunter"}}.items():
        if cfg[key]["provider"] not in allowed:
            raise ValueError(f"Unsupported {key} provider")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,80}", cfg[key]["api_key_env"]):
            raise ValueError("api_key_env must name an environment variable, never contain a secret")
    for key in ("model", "search"):
        parsed, _ = validate_url(cfg[key]["endpoint"], local=True)
        if parsed.query or parsed.fragment:
            raise ValueError("Provider endpoints must not contain query strings or fragments")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote provider endpoints require HTTPS")
    if not isinstance(cfg["model"]["model"], str) or not cfg["model"]["model"].strip():
        raise ValueError("Configure a model name")
    ceilings = {"companies": 500, "contacts": 50, "competitors": 15, "search_requests": 500,
                "model_calls": 1000, "pages": 2000, "domain_searches": 500, "email_verifications": 1000}
    for key, maximum in ceilings.items():
        if type(cfg["limits"][key]) is not int or not 0 <= cfg["limits"][key] <= maximum:
            raise ValueError(f"limits.{key} must be between 0 and {maximum}")
    if cfg["limits"]["companies"] < 1 or cfg["limits"]["contacts"] < 1:
        raise ValueError("Company and contact targets must be positive")
    for key in ("trigger_window_days",):
        if type(cfg[key]) is not int or not 1 <= cfg[key] <= 365:
            raise ValueError(f"{key} must be between 1 and 365")
    if not isinstance(cfg["company_urls"], list) or len(cfg["company_urls"]) > 500:
        raise ValueError("company_urls must be a list of at most 500 URLs")
    for url in cfg["company_urls"]:
        validate_url(url)
    return cfg


def ident(value):
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def host(url):
    value = urlsplit(url).hostname or ""
    return value.lower().removeprefix("www.")


def norm(value):
    return " ".join(str(value).split())


def string(value, length=1500):
    return value[:length].strip() if isinstance(value, str) else ""


def strings(value, limit=10):
    return [string(x) for x in value[:limit] if isinstance(x, str) and x.strip()] if isinstance(value, list) else []


def source_text(source):
    return source.get("text", "") + "\n" + "\n".join(source.get("links", []))


def proof(raw, sources, *, literal=None, page_only=False):
    if not isinstance(raw, dict):
        return []
    url, quote = raw.get("source_url"), raw.get("quote")
    source = next((s for s in sources if s["url"] == url), None)
    if source is None or not isinstance(quote, str) or len(norm(quote)) < 8:
        return []
    if page_only and source.get("kind") != "page":
        return []
    if norm(quote) not in norm(source_text(source)):
        return []
    if literal and norm(literal).casefold() not in norm(quote).casefold():
        return []
    return [{"source_url": url, "quote": quote[:2000], "checked_at": source["fetched_at"]}]


def fact(raw, sources, *, page_only=False):
    if not isinstance(raw, dict) or not isinstance(raw.get("value"), str):
        return None, []
    value = string(raw["value"], 500)
    evidence = proof(raw, sources, literal=value, page_only=page_only)
    return (value, evidence) if value and evidence else (None, [])


def linkedin(value, company=False):
    if not isinstance(value, str):
        return None
    p = urlsplit(value)
    prefix = "/company/" if company else "/in/"
    return value if p.scheme == "https" and host(value) == "linkedin.com" and p.path.startswith(prefix) else None


def fresh_email(contact, days=30):
    if contact.get("do_not_contact") or contact.get("email_status") != "valid" or not contact.get("email_provider"):
        return False
    if not EMAIL.fullmatch(contact.get("email") or ""):
        return False
    try:
        checked = datetime.fromisoformat(contact["email_checked_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        return 0 <= age <= min(days, 30) * 86400
    except (KeyError, ValueError, TypeError):
        return False


def summarise(bundle):
    companies = bundle.get("companies", [])
    contacts = [x for c in companies for x in c.get("contacts", [])]
    supported = [c for c in companies if c.get("identity_status") == "source_supported"]
    ready = [c for c in supported if c.get("fit_status") == "potential_fit" and
             any(fresh_email(x) for x in c.get("contacts", []))]
    enriched = [c for c in ready if c.get("linkedin") and any(t.get("status") == "source_supported" for t in c.get("tech_stack", []))]
    named, seen = [], set()
    for contact in contacts:
        key = contact.get("linkedin") or contact.get("email") or contact.get("id")
        if contact.get("name") and key not in seen and not contact.get("do_not_contact"):
            named.append(contact["id"])
            seen.add(key)
    bundle["people_shortlist"] = named[:bundle["targets"]["people_max"]]
    result = {"companies": len(companies), "source_supported_companies": len(supported),
              "outreach_ready_companies": len(ready), "enriched_companies": len(enriched),
              "people": len(bundle["people_shortlist"]),
              "verified_emails": len({x["email"].lower() for x in contacts if fresh_email(x)}),
              "competitors": len(bundle.get("competitors", [])), "segments": len(bundle.get("segments", [])),
              "sequences": len(bundle.get("sequences", []))}
    target = bundle["targets"]
    requirements = {"enriched_companies": target["companies"], "people": target["people_min"],
                    "competitors": target["competitors"], "segments": target["segments"],
                    "sequences": target["segments"]*target["sequences_per_segment"]}
    result["shortfalls"] = [f"{key}: {result[key]}/{count}" for key, count in requirements.items() if result[key] < count]
    result["target_met"] = not result["shortfalls"] and bundle.get("mode") == "live"
    return result


def new_bundle(url, cfg):
    return {"schema_version": 2, "id": ident(url+now()), "mode": "live", "status": "running",
            "stage": "product", "created_at": now(), "updated_at": now(), "product_url": url,
            "targets": {"companies": cfg["limits"]["companies"], "competitors": cfg["limits"]["competitors"],
                        "segments": 6, "people_min": min(30, cfg["limits"]["contacts"]),
                        "people_max": cfg["limits"]["contacts"], "sequences_per_segment": 3},
            "product": {}, "segments": [], "companies": [], "competitors": [], "sequences": [],
            "sources": [], "warnings": [], "usage": {}, "summary": {}}


def save_bundle(output, bundle):
    path = Path(output)
    path.mkdir(parents=True, exist_ok=True)
    bundle["summary"] = summarise(bundle)
    bundle["updated_at"] = now()
    atomic_text(path/"bundle.json", json.dumps(bundle, ensure_ascii=False, indent=2)+"\n")
    from .exports import export_csv
    for kind in ("companies", "contacts", "competitors", "sequences", "triggers"):
        atomic_text(path/(kind+".csv"), export_csv(bundle, kind))
    atomic_text(path/"report.html", render_bundle(bundle))


def render_bundle(bundle):
    e = lambda v: html.escape(str(v or ""), quote=True)
    def evidence_list(rows):
        return "".join(f'<li><a href="{e(x["source_url"])}" rel="noreferrer">Source</a>: {e(x["quote"])}</li>' for x in rows)
    cards = []
    for c in bundle["companies"]:
        contacts = "".join(f'<li>{e(x.get("name") or "Business contact")} · {e(x.get("role"))} · {e(x.get("email"))} · {e(x.get("email_status"))}</li>' for x in c["contacts"])
        cards.append(f'<article><h2>{e(c["name"])}</h2><a href="{e(c["website"])}">{e(c["domain"])}</a><p>{e(c["fit_status"])} · {e(c["fit_reason"])}</p><ul>{contacts}</ul><details><summary>Evidence</summary><ul>{evidence_list(c["evidence"])}</ul></details></article>')
    rivals = "".join(f'<article><h3>{e(c["name"])}</h3><p>{e(c["relationship"])} · {e(c["reason"])}</p><p>Pricing: {e(c.get("pricing") or "Unknown")}</p><ul>{evidence_list(c["evidence"])}</ul></article>' for c in bundle["competitors"])
    sequences = "".join(f'<article><h3>{e(s["name"])} · {e(s["channel"])}</h3>'+"".join(f'<p><b>Day {e(x["day"])} · {e(x["subject"])}</b></p><pre>{e(x["body"])}</pre>' for x in s["steps"])+"</article>" for s in bundle["sequences"])
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Open Buyer Finder · Research pack</title>
<style>body{{font:16px/1.6 system-ui;color:#14263d;background:#f5f4ef;margin:0}}main{{max-width:1000px;margin:auto;padding:32px}}header{{background:#14263d;color:white;padding:32px;border-radius:18px}}article{{background:white;padding:24px;border:1px solid #deded5;border-radius:12px;margin:16px 0}}a{{color:#187465}}pre{{white-space:pre-wrap;font:inherit}}p,li,a{{overflow-wrap:anywhere}}aside{{background:#fff0cb;padding:16px}}h1{{line-height:1.2}}</style>
<main><header><small>OPEN BUYER FINDER · {e(bundle['mode'])}</small><h1>{e(bundle.get('product',{}).get('name') or 'Research pack')}</h1><p>{e(bundle['status'])} · {e(bundle['stage'])}</p></header>
<p>{e(bundle['summary']['companies'])} companies · {e(bundle['summary']['outreach_ready_companies'])} outreach-ready · {e(bundle['summary']['people'])} named prospects · {e(bundle['summary']['verified_emails'])} provider-verified emails</p>
<aside>These are research candidates, not confirmed customers. Source support is not proof of purchasing intent. Campaigns are drafts; no conversion performance is established.</aside>
<p>Shortfalls: {e('; '.join(bundle['summary']['shortfalls']) or 'None')}</p><p>{e('; '.join(bundle['warnings']))}</p>
<h2>Companies</h2>{''.join(cards)}<h2>Competitors</h2>{rivals}<h2>Draft campaigns</h2>{sequences}<footer>Generated {e(bundle['updated_at'])}. Full fields, signals and provenance are in bundle.json. Use the local studio for filtering and exports.</footer></main></html>'''


class Pipeline:
    def __init__(self, url, config, output, progress=None, providers=None):
        self.cfg, self.output, self.progress = validate_config(config), Path(output), progress
        validate_url(url)
        if self.output.exists():
            raise ValueError("Output already exists; choose a new directory")
        self.p = providers or Providers(self.cfg)
        self.b = new_bundle(url, self.cfg)
        self.visited, self.sources = set(), {}

    def checkpoint(self, stage):
        self.b["stage"] = stage
        self.b["usage"] = dict(self.p.usage)
        self.b["sources"] = list(self.sources.values())
        save_bundle(self.output, self.b)
        if self.progress:
            self.progress(copy.deepcopy(self.b))

    def warn(self, message):
        if message not in self.b["warnings"]:
            self.b["warnings"].append(message)

    def add(self, source):
        old = self.sources.get(source["url"])
        if old is None or source.get("kind") == "page":
            self.sources[source["url"]] = source
        return source

    def search(self, query):
        try:
            return [self.add(s) for s in self.p.search(query)]
        except FetchError as exc:
            self.warn(f"Search: {exc}")
            return []

    def pages(self, url, count=3):
        result = []
        try:
            first = self.add(self.p.page(url))
            result.append(first)
            terms = ("pricing", "about", "team", "contact", "careers", "jobs", "integrations", "customers")
            candidates = [u for u in first.get("links", []) if u.startswith(("https://", "http://")) and
                          host(u) == host(first["url"]) and u != first["url"] and any(t in urlsplit(u).path.lower() for t in terms)]
            candidates.sort(key=lambda u: next((i for i,t in enumerate(terms) if t in urlsplit(u).path.lower()), 99))
            for link in list(dict.fromkeys(candidates))[:count-1]:
                try:
                    result.append(self.add(self.p.page(link)))
                except FetchError as exc:
                    self.warn(f"Page {host(link)}: {exc}")
        except FetchError as exc:
            self.warn(f"Page {host(url)}: {exc}")
        return result

    def product(self):
        sources = self.pages(self.b["product_url"], 3)
        if not sources:
            raise FetchError("Product URL could not be read. Use an accessible public product page.")
        data = self.p.ask("PRODUCT: Extract product {name,description,capabilities:[fact],exclusions:[]} and exactly six distinct segments [{id,name,buyer_role,pain,include:[],exclude:[],queries:[four specific company-search queries],angle}]. name and description are fact objects. Capabilities must use exact evidence. Segment choices are proposed hypotheses. Return {product,segments,competitor_queries:[three search queries]}. Do not invent product capabilities.", {"sources": sources})
        raw = data.get("product", {})
        name, name_proof = fact(raw.get("name"), sources, page_only=True)
        description, desc_proof = fact(raw.get("description"), sources, page_only=True)
        if not name or not description:
            raise FetchError("Product extraction lacks exact sourced name/description; adjust the model or source URL")
        caps = [fact(x, sources)[0] for x in raw.get("capabilities", []) if isinstance(x, dict)]
        self.b["product"] = {"name": name, "description": description, "capabilities": [x for x in caps if x],
                             "exclusions": strings(raw.get("exclusions")), "source_urls": [s["url"] for s in sources],
                             "evidence": name_proof+desc_proof}
        segments = data.get("segments", [])
        if not isinstance(segments, list):
            raise FetchError("Model returned invalid segments")
        for i, row in enumerate(segments[:6]):
            if not isinstance(row, dict) or not all(string(row.get(k)) for k in ("name", "buyer_role", "pain")):
                continue
            segment = {k:string(row.get(k)) for k in ("name", "buyer_role", "pain", "angle")}
            segment.update(id=f"segment-{i+1}", include=strings(row.get("include")), exclude=strings(row.get("exclude")),
                           queries=strings(row.get("queries"), 4))
            self.b["segments"].append(segment)
        if len(self.b["segments"]) != 6:
            self.warn("Model did not produce six valid segments; missing segments are not fabricated")
        return strings(data.get("competitor_queries"), 3)

    def candidates(self, results, kind, segment=None):
        if not results:
            return []
        data = self.p.ask("CANDIDATES: Select up to 20 relevant "+kind+" company websites from these observed search results. Return {candidates:[{website,reason,relationship:'direct'|'adjacent'}]}. website MUST be one of the supplied source URLs or an observed link, never a guessed URL. For target companies exclude competitors, directories, press, universities unless relevant, and generic social profiles.",
                          {"product": self.b["product"], "segment": segment, "sources": results})
        allowed = {s["url"] for s in results} | {u for s in results for u in s.get("links", [])}
        rows = data.get("candidates", [])
        if not isinstance(rows, list):
            raise FetchError("Model returned malformed company candidates")
        output = []
        for row in rows[:20]:
            if not isinstance(row, dict) or row.get("website") not in allowed:
                continue
            url = row["website"]
            try:
                validate_url(url)
            except FetchError:
                continue
            if host(url) in {host(self.b["product_url"]), "linkedin.com", "facebook.com", "github.com", "x.com", "reddit.com"}:
                continue
            output.append(row)
        return output

    def contacts(self, raw, sources, domain):
        result, seen = [], set()
        for row in raw[:5] if isinstance(raw, list) else []:
            if not isinstance(row, dict):
                continue
            name, ep1 = fact(row.get("name"), sources)
            role, ep2 = fact(row.get("role"), sources)
            profile, ep3 = fact(row.get("linkedin"), sources)
            email, ep4 = fact(row.get("email"), sources)
            if email and (not EMAIL.fullmatch(email) or email.rsplit("@",1)[1].lower() != domain):
                email, ep4 = None, []
            if not name and not email:
                continue
            key = email.lower() if email else (name or "")+str(profile)
            if key in seen:
                continue
            seen.add(key)
            result.append({"id": ident(domain+key), "name": name or "", "role": role or "", "linkedin": linkedin(profile),
                           "email": email, "email_status": "published_unverified" if email else "missing", "email_provider": None,
                           "email_checked_at": None, "evidence": ep1+ep2+ep3+ep4, "do_not_contact": False})
        # A published role address remains a role address; do not invent its owner.
        for source in sources:
            for email in EMAIL.findall(source_text(source)):
                if email.rsplit("@",1)[1].lower() != domain or email.lower() in seen or len(result) >= 5:
                    continue
                seen.add(email.lower())
                result.append({"id":ident(domain+email.lower()), "name":"", "role":"Published business address",
                    "linkedin":None, "email":email, "email_status":"published_unverified", "email_provider":None,
                    "email_checked_at":None, "evidence":[{"source_url":source["url"],"quote":email,"checked_at":source["fetched_at"]}],"do_not_contact":False})
        try:
            enriched = self.p.enrich(domain)
            for row in enriched[:5]:
                email = row.get("value")
                if not isinstance(email,str) or not EMAIL.fullmatch(email) or email.rsplit("@",1)[1].lower()!=domain:
                    continue
                if email.lower() in seen:
                    continue
                seen.add(email.lower())
                result.append({"id":ident(domain+email.lower()), "name":" ".join(filter(None,[row.get("first_name"),row.get("last_name")])),
                    "role":string(row.get("position")), "linkedin":linkedin(row.get("linkedin")), "email":email,
                    "email_status":"published_unverified", "email_provider":None,"email_checked_at":None,
                    "evidence":[{"source_url":"https://hunter.io/domain-search?domain="+domain,"quote":"Contact returned by Hunter domain search; ownership not independently confirmed.","checked_at":now()}],"do_not_contact":False})
        except FetchError as exc:
            self.warn(f"Enrichment: {exc}")
        result.sort(key=lambda x: (not bool(x.get("name")), not bool(x.get("email"))))
        verified_attempts = 0
        for contact in result:
            if not contact["email"]:
                continue
            # Spread a bounded verification budget across companies, not all contacts
            # at the first few domains. Extra addresses remain explicitly unverified.
            if verified_attempts >= 1:
                continue
            try:
                verified_attempts += 1
                check = self.p.verify(contact["email"])
                contact.update(email_status=check["status"], email_provider=check["provider"], email_checked_at=check["checked_at"])
            except FetchError as exc:
                self.warn(f"Email verification: {exc}")
        return result

    def triggers(self, raw, sources, company_id):
        result = []
        for row in raw[:10] if isinstance(raw, list) else []:
            if not isinstance(row, dict) or row.get("kind") not in {"hiring", "funding", "launch", "technology"}:
                continue
            ep = proof(row, sources)
            if not ep:
                continue
            event_date, status = row.get("event_date"), "undated_signal"
            try:
                parsed = date.fromisoformat(event_date)
                age = (datetime.now(timezone.utc).date()-parsed).days
                # A date must occur in the cited evidence in ISO form; guessed dates stay unknown.
                if age < 0 or event_date not in row["quote"]:
                    event_date = None
                else:
                    status = "dated_signal" if age <= self.cfg["trigger_window_days"] else "stale_signal"
            except (ValueError, TypeError):
                event_date = None
            summary = string(row.get("summary"))
            result.append({"id":ident(company_id+row["kind"]+summary),"company_id":company_id,"kind":row["kind"],
                           "summary":summary,"event_date":event_date,"evidence":ep,"status":status})
        return result

    def company(self, candidate, segment):
        url = candidate["website"]
        sources = self.pages(url, 4)
        if not sources:
            return None
        domain = host(sources[0]["url"])
        if domain in self.visited:
            return None
        self.visited.add(domain)
        data = self.p.ask("COMPANY: Analyse this company against product and all proposed segments; select its best matching segment_id. Return {name:fact,industry:fact,location:fact,linkedin:fact,segment_id,fit_status:'potential_fit'|'needs_context'|'not_fit',fit_reason,fit_evidence:{source_url,quote},tech_stack:[fact],contacts:[{name:fact,role:fact,linkedin:fact,email:fact}],triggers:[{kind,summary,event_date,source_url,quote}]}. Use null for missing facts; never infer hidden backend technology from a marketing website. Distinguish a company's own stated capabilities from its unmet need. No proof of purchase intent. Event dates must be explicitly sourced; use null if undated.",
                          {"product":self.b["product"],"segment":segment,"segments":self.b["segments"],"sources":sources})
        name, ep = fact(data.get("name"), sources, page_only=True)
        if not name:
            self.warn(f"Company {domain}: no source-supported name; excluded")
            return None
        fit_ep = proof(data.get("fit_evidence"),sources,page_only=True)
        status = data.get("fit_status")
        if status not in {"potential_fit","needs_context","not_fit"} or not fit_ep:
            status = "needs_context"
        industry, ep2 = fact(data.get("industry"),sources)
        location, ep3 = fact(data.get("location"),sources)
        profile, ep4 = fact(data.get("linkedin"),sources)
        stack=[]
        for raw in data.get("tech_stack",[])[:12]:
            value,e = fact(raw,sources)
            if value:
                stack.append({"name":value,"evidence":e,"status":"source_supported"})
        company_id=ident(domain)
        segment_id=data.get("segment_id") if data.get("segment_id") in {s["id"] for s in self.b["segments"]} else segment["id"]
        return {"id":company_id,"name":name,"domain":domain,"website":sources[0]["url"],"segment_id":segment_id,
                "fit_status":status,"fit_reason":string(data.get("fit_reason")),"identity_status":"source_supported",
                "industry":industry,"location":location,"linkedin":linkedin(profile,company=True),"tech_stack":stack,
                "evidence":ep+ep2+ep3+ep4+fit_ep,"contacts":self.contacts(data.get("contacts",[]),sources,domain),
                "triggers":self.triggers(data.get("triggers",[]),sources,company_id),"checked_at":now()}

    def competitor(self, candidate):
        sources=self.pages(candidate["website"],3)
        if not sources:
            return None
        reviews=self.search('"'+sources[0]["title"][:100]+'" reviews limitations pricing')
        for item in reviews[:2]:
            sources.extend(self.pages(item["url"],1))
        data=self.p.ask("COMPETITOR: Return {name:fact,pricing:fact,features:[fact],relationship:'direct'|'adjacent',reason,review_gaps:[{complaint,marketing_angle,source_url,quote,review_date}]}. Pricing must come from official company pages. Reviews must be actual attributed user complaints, not your imagined feature gaps or a competitor's marketing. A missing review remains absent. Marketing angles are hypotheses consistent with our product's evidenced capabilities.",
                        {"product":self.b["product"],"candidate":candidate,"sources":sources})
        official=[s for s in sources if host(s["url"])==host(candidate["website"])]
        name,ep=fact(data.get("name"),official,page_only=True)
        if not name:
            return None
        price,pep=fact(data.get("pricing"),official,page_only=True)
        features=[fact(x,official)[0] for x in data.get("features",[])[:15]]
        gaps=[]
        for row in data.get("review_gaps",[])[:8]:
            ev=proof(row,sources,page_only=True)
            if ev:
                gaps.append({"complaint":string(row.get("complaint")),"marketing_angle":string(row.get("marketing_angle")),
                             "evidence":ev,"review_date":string(row.get("review_date")) or None})
        return {"id":ident(host(candidate["website"])),"name":name,"website":candidate["website"],
                "relationship":data.get("relationship") if data.get("relationship") in {"direct","adjacent"} else "adjacent",
                "reason":string(data.get("reason")),"pricing":price,"features":[x for x in features if x],
                "review_gaps":gaps,"evidence":ep+pep,"checked_at":now()}

    def campaigns(self):
        for segment in self.b["segments"]:
            data=self.p.ask("SEQUENCES: Return {sequences:[{name,channel,steps:[{day,subject,body}]}]} with exactly three distinct sequences, each with three steps (days 0,3,7). Two email variants and one LinkedIn draft variant. Plain, personal, concise, no invented customers, success metrics, familiarity or 'high-converting' claims. Allowed placeholders: {{first_name}}, {{company_name}}, {{product_name}}, {{fit_reason}}, {{sender_name}}. Include a simple opt-out sentence in every email. Use only evidenced product capabilities. Output is unapproved draft copy; do not send.",
                            {"product":self.b["product"],"segment":segment})
            for i,row in enumerate(data.get("sequences",[])[:3]):
                if not isinstance(row,dict) or row.get("channel") not in {"email","linkedin"}:
                    continue
                steps=row.get("steps",[])
                if not isinstance(steps,list) or len(steps)!=3:
                    continue
                cleaned=[]
                for j,step in enumerate(steps):
                    if not isinstance(step,dict) or not string(step.get("body")):
                        break
                    body=string(step["body"],2500)
                    unknown=set(re.findall(r"\{\{\s*(\w+)\s*\}\}",body+string(step.get("subject"))))-{"first_name","company_name","product_name","fit_reason","sender_name"}
                    if unknown:
                        break
                    cleaned.append({"day":(0,3,7)[j],"subject":string(step.get("subject"),200),"body":body})
                if len(cleaned)==3:
                    self.b["sequences"].append({"id":segment["id"]+f"-sequence-{i+1}","segment_id":segment["id"],
                       "name":string(row.get("name")) or f"Variant {i+1}","channel":row["channel"],"status":"draft","steps":cleaned})
            self.checkpoint("campaigns")

    def run(self):
        try:
            self.checkpoint("product")
            competitor_queries=self.product()
            self.checkpoint("segments")
            self.campaigns()
            self.checkpoint("companies")
            target=self.b["targets"]["companies"]
            def enough():
                return summarise(self.b)["enriched_companies"]>=target or len(self.b["companies"])>=min(500,max(100,target*3))
            # Seed URLs are user-selected candidates, still requiring source-backed review.
            for segment in self.b["segments"]:
                queries=segment["queries"]
                batches=[[{"website":u} for u in self.cfg["company_urls"]]] if segment==self.b["segments"][0] and self.cfg["company_urls"] else []
                for query in ([None] if batches else []) + queries:
                    if enough():
                        break
                    if query:
                        batches.append(self.candidates(self.search(query),"target",segment))
                    # Process each query immediately, preserving progress if later requests fail.
                    while batches:
                        for candidate in batches.pop(0):
                            if enough():
                                break
                            if host(candidate["website"]) in self.visited:
                                continue
                            try:
                                company=self.company(candidate,segment)
                                if company and company["fit_status"]!="not_fit":
                                    self.b["companies"].append(company)
                                elif company:
                                    self.warn(f"Excluded non-fit company: {company['domain']}")
                            except BudgetExceeded:
                                raise
                            except (FetchError,TypeError,KeyError,AttributeError) as exc:
                                self.warn(f"Company extraction: {exc}" if isinstance(exc,FetchError) else "Company extraction returned malformed data")
                            self.checkpoint("companies")
            self.checkpoint("competitors")
            rivals=set()
            for query in competitor_queries:
                if len(self.b["competitors"])>=self.b["targets"]["competitors"]:
                    break
                for candidate in self.candidates(self.search(query),"competitor"):
                    domain=host(candidate["website"])
                    if domain in rivals or len(rivals)>=self.b["targets"]["competitors"]:
                        continue
                    try:
                        competitor=self.competitor(candidate)
                        if competitor:
                            rivals.add(domain)
                            self.b["competitors"].append(competitor)
                    except BudgetExceeded:
                        raise
                    except (FetchError,TypeError,KeyError,AttributeError):
                        self.warn(f"Competitor {domain}: extraction incomplete")
                    self.checkpoint("competitors")
            self.b["status"]="complete" if summarise(self.b)["target_met"] and not self.b["warnings"] else "partial"
        except (FetchError,TypeError,KeyError,AttributeError,ValueError) as exc:
            self.warn(str(exc) if isinstance(exc,(FetchError,ValueError)) else "Provider output did not match the required structure")
            self.b["status"]="partial" if self.b["product"] else "blocked"
        self.checkpoint("finished")
        return self.b


def run_pipeline(url, config, output, progress=None, *, providers=None):
    return Pipeline(url,config,output,progress,providers).run()


def monitor_run(directory, config, *, providers=None):
    path=Path(directory)
    bundle=json.loads((path/"bundle.json").read_text(encoding="utf-8"))
    if bundle.get("mode")!="live":
        raise ValueError("Synthetic demo cannot be monitored as live research")
    cfg=validate_config(config)
    p=providers or Providers(cfg)
    runner=object.__new__(Pipeline)
    runner.cfg,runner.b,runner.p,runner.sources=cfg,bundle,p,{s["url"]:s for s in bundle["sources"]}
    new=[]
    for company in bundle["companies"]:
        try:
            results=runner.search('"'+company["name"]+'" hiring funding launch')
            sources=[]
            for result in results[:3]:
                sources.extend(runner.pages(result["url"],1))
            if not sources:
                continue
            data=p.ask("TRIGGERS: Return {triggers:[{kind,summary,event_date,source_url,quote}]}. Extract only explicit hiring, funding, launch or technology events for this exact company. Dates must be sourced; missing dates are null. Do not confuse similarly named companies. Signals do not establish buying intent.",{"company":company["name"],"domain":company["domain"],"sources":sources})
            old={t["id"] for t in company["triggers"]}
            for trigger in runner.triggers(data.get("triggers",[]),sources,company["id"]):
                if trigger["id"] not in old:
                    company["triggers"].append(trigger)
                    new.append(trigger)
        except BudgetExceeded as exc:
            runner.warn(str(exc));break
        except (FetchError,TypeError,KeyError):
            runner.warn(f"Trigger refresh incomplete for {company['domain']}")
    bundle["sources"]=list(runner.sources.values())
    bundle.setdefault("monitor_history",[]).append({"checked_at":now(),"new_signals":len(new),"usage":dict(p.usage)})
    # Preserve the preceding snapshot before replacing the live report.
    snapshot=path/("before-monitor-"+ident(now())+".json")
    atomic_text(snapshot,(path/"bundle.json").read_text(encoding="utf-8"))
    save_bundle(path,bundle)
    return bundle


def demo_bundle():
    """Deterministic fictional examples. Never passed off as a live acceptance run."""
    cfg=load_config()
    b=new_bundle("https://fourgate.example",cfg)
    b.update(mode="synthetic_demo",status="partial",stage="finished",product={"name":"Fourgate · fictional demo",
        "description":"Check whether an agent write matches its expected saved state.","capabilities":["Independent read-back"],"exclusions":["Read-only search"],"source_urls":[]})
    names=["Automation agencies","Agent product teams","Internal operations teams","CRM integration builders","Support workflow teams","Developer tool teams"]
    for i,name in enumerate(names):
        sid=f"segment-{i+1}"
        b["segments"].append({"id":sid,"name":name,"buyer_role":"Workflow owner","pain":"Manual checking after agent writes",
                              "include":["A staging write workflow"],"exclude":["Read-only search"],"queries":[],"angle":"Understand the current checking process"})
        for j,channel in enumerate(("email","email","linkedin")):
            b["sequences"].append({"id":f"{sid}-sequence-{j+1}","segment_id":sid,"name":["Workflow question","Checking method","Discovery conversation"][j],"channel":channel,"status":"draft","steps":[
                {"day":day,"subject":"How do you check agent writes?" if channel=="email" else "",
                 "body":"Hi {{first_name}}, I’m {{sender_name}}, building {{product_name}}. How does {{company_name}} check that agent writes actually persist? Happy to compare approaches. If this is not relevant, let me know and I won’t follow up." if day==0 else "Following up on your checking process. Is there one staging workflow where independent read-back would be useful? No worries if this is not relevant; I won’t follow up if you prefer."} for day in (0,3,7)]})
    for i in range(12):
        domain=f"demo-company-{i+1}.example"
        ev=[{"source_url":"https://"+domain,"quote":"Fictional example: this team builds agent workflows and uses PostgreSQL.","checked_at":now()}]
        contact={"id":f"contact-{i+1}","name":f"Demo person {i+1}","role":"Workflow owner","linkedin":None,
                 "email":f"demo@{domain}","email_status":"published_unverified","email_provider":None,"email_checked_at":None,"evidence":ev,"do_not_contact":False}
        b["companies"].append({"id":ident(domain),"name":f"Fictional Company {i+1}","domain":domain,"website":"https://"+domain,
            "segment_id":f"segment-{i%6+1}","fit_status":"needs_context" if i%3==0 else "potential_fit", "fit_reason":"Illustrative workflow fit; real need and authority remain unknown.",
            "identity_status":"unknown","industry":"Fictional automation business","location":None,"linkedin":None,"tech_stack":[{"name":"PostgreSQL","status":"unknown","evidence":ev}],
            "contacts":[contact],"evidence":ev,"triggers":[],"checked_at":now()})
    for i in range(15):
        b["competitors"].append({"id":f"rival-{i+1}","name":f"Fictional Alternative {i+1}","website":f"https://alternative-{i+1}.example",
            "relationship":"direct" if i<5 else "adjacent","reason":"Illustrative comparison; this is not a real competitor claim.","pricing":None,"features":["Illustrative feature"],"review_gaps":[],"evidence":[],"checked_at":now()})
    b["warnings"]=["Every company, person and competitor in this demo is fictional. No email is verified. External exports and sending are blocked."]
    b["summary"]=summarise(b)
    return b
