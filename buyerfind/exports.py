"""Reviewable exports and explicit campaign activation, using only the stdlib.

Preparing an export is local. Applying one changes the selected external account.
Activation is a separate operation. No operation retries an ambiguous write.
"""
import copy
import csv
import hashlib
import html
import io
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ExportError(ValueError):
    """Invalid export, unmet precondition, or an outcome requiring reconciliation."""


BASES = {"hubspot": "https://api.hubapi.com", "notion": "https://api.notion.com",
         "instantly": "https://api.instantly.ai"}
ENV = {"hubspot": "HUBSPOT_ACCESS_TOKEN", "notion": "NOTION_API_KEY", "instantly": "INSTANTLY_API_KEY"}
TOKENS = {"first_name", "company_name", "product_name", "fit_reason", "sender_name"}
NOTION_PROPERTIES = {"Name": "title", "Domain": "rich_text", "Website": "url",
                     "Fit reason": "rich_text", "Segment": "rich_text", "Evidence": "rich_text"}


def _time(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ExportError("A timezone is required for verification timestamps")
    return result.astimezone(timezone.utc)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _seal(plan):
    plan["approval_hash"] = _hash(plan)
    return plan


def _validate_hash(plan, approval):
    actual = _hash({k: v for k, v in plan.items() if k != "approval_hash"})
    if actual != plan.get("approval_hash") or approval != actual:
        raise ExportError("Exact approval_hash required; the plan may have changed")


def _cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = "" if value is None else str(value)
    # Quoting CSV alone does not stop spreadsheet formula execution.
    return "'" + text if text.lstrip(" \t\r\n\x00\ufeff").startswith(("=", "+", "-", "@")) else text


def _sources(row):
    return "; ".join(dict.fromkeys(e.get("source_url", "") for e in row.get("evidence", [])))


def _contacts(bundle):
    for company in bundle.get("companies", []):
        for contact in company.get("contacts", []):
            yield company, contact


def _eligible(company, contact, suppressed, at):
    email = str(contact.get("email") or "").strip().casefold()
    if contact.get("do_not_contact") or email in suppressed:
        return "suppressed"
    if company.get("identity_status") != "source_supported" or company.get("fit_status") != "potential_fit":
        return "company identity or fit not supported"
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", email):
        return "missing or malformed email"
    if contact.get("email_status") != "valid" or not str(contact.get("email_provider") or "").strip():
        return "email is not provider-verified valid"
    try:
        age = at - _time(contact.get("email_checked_at") or "invalid")
    except (ValueError, TypeError):
        return "missing verification timestamp"
    if not timedelta(0) <= age <= timedelta(days=30):
        return "email verification stale or future dated"
    return None


def export_csv(bundle, kind="companies"):
    """Export research tables or import-shaped tables; no network or account writes."""
    mode = bundle.get("mode", "unknown")
    companies = bundle.get("companies", [])
    rows = []
    if kind in {"companies", "notion"}:
        fields = ["id", "name", "domain", "website", "segment_id", "fit_status", "identity_status",
                  "fit_reason", "industry", "location", "linkedin", "tech_stack", "evidence", "checked_at"]
        rows = [{**c, "evidence": _sources(c)} for c in companies]
        if kind == "notion":
            fields = list(NOTION_PROPERTIES)
            rows = [{"Name": c.get("name"), "Domain": c.get("domain"), "Website": c.get("website"),
                     "Fit reason": c.get("fit_reason"), "Segment": c.get("segment_id"),
                     "Evidence": _sources(c)} for c in companies]
    elif kind in {"contacts", "hubspot", "instantly"}:
        fields = ["id", "company_name", "name", "role", "linkedin", "email", "email_status",
                  "email_provider", "email_checked_at", "do_not_contact", "evidence"]
        if kind == "hubspot":
            fields = ["Email", "First Name", "Last Name", "Company Name", "Job Title", "Website URL"]
        elif kind == "instantly":
            fields = ["email", "first_name", "last_name", "company_name", "website", "fit_reason"]
        for company, contact in _contacts(bundle):
            if kind in {"hubspot", "instantly"} and _eligible(company, contact, set(), _time()):
                continue
            row = {**contact, "company_name": company.get("name"), "evidence": _sources(contact)}
            if kind == "hubspot":
                parts = (contact.get("name") or "").split(maxsplit=1)
                row = {"Email": contact.get("email"), "First Name": parts[0] if parts else "",
                       "Last Name": parts[1] if len(parts) > 1 else "", "Company Name": company.get("name"),
                       "Job Title": contact.get("role"), "Website URL": company.get("website")}
            if kind == "instantly":
                parts = (contact.get("name") or "").split(maxsplit=1)
                row.update(first_name=parts[0] if parts else "", last_name=parts[1] if len(parts) > 1 else "",
                           website=company.get("website"), fit_reason=company.get("fit_reason"))
            rows.append(row)
    elif kind == "sequences":
        fields = ["id", "segment_id", "name", "channel", "status", "day", "subject", "body"]
        rows = [{**sequence, **step} for sequence in bundle.get("sequences", [])
                for step in sequence.get("steps", [])]
    elif kind == "competitors":
        fields = ["id", "name", "website", "relationship", "reason", "pricing", "features", "review_gaps", "evidence"]
        rows = [{**c, "evidence": _sources(c)} for c in bundle.get("competitors", [])]
    elif kind == "triggers":
        fields = ["id", "company_id", "company_name", "kind", "summary", "event_date", "status", "evidence"]
        rows = [{**t, "company_name": c.get("name"), "evidence": _sources(t)}
                for c in companies for t in c.get("triggers", [])]
    else:
        raise ExportError("Unknown CSV kind")
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["mode"] + fields)
    for row in rows:
        writer.writerow([_cell(mode)] + [_cell(row.get(key)) for key in fields])
    return stream.getvalue()


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ExportError(name + " must be an explicit provider identifier")
    return value


def _render(template, variables):
    if not isinstance(template, str) or not template.strip():
        raise ExportError("Sequence subject and body must be nonempty")
    def replace(match):
        key = match.group(1).strip()
        if key not in TOKENS or not str(variables.get(key) or "").strip():
            raise ExportError("Unknown or missing personalization token: " + key)
        return str(variables[key])
    output = re.sub(r"{{\s*([^{}]+?)\s*}}", replace, template)
    if "{{" in output or "}}" in output:
        raise ExportError("Malformed or unresolved personalization token")
    return output


def _subject_template(template):
    # Subject headers are plain text; HTML-escaped merge values are only for bodies.
    return re.sub(r"{{\s*([^{}]+?)\s*}}", lambda match: "{{obf_subject_" + match.group(1).strip() + "}}",
                  str(template or ""))


def _rich(value, kind="rich_text"):
    text = str(value or "")
    # Each rich text item is at most 2,000 characters. Preserve text, don't truncate it.
    return {kind: [{"type": "text", "text": {"content": text[i:i + 2000]}}
                   for i in range(0, len(text), 2000)]}


def prepare_export(bundle, destination, options=None, *, now=None):
    """Create a hash-bound, credential-free request plan without contacting providers."""
    if bundle.get("mode") != "live":
        raise ExportError("External export of synthetic or unknown-mode data is blocked")
    if destination not in BASES:
        raise ExportError("Destination must be hubspot, notion, or instantly")
    options = copy.deepcopy(options or {})
    allowed = {"contact_ids", "company_ids", "suppress_emails", "sender_name", "sequence_id",
               "campaign_id", "data_source_id", "daily_limit"}
    if set(options) - allowed:
        raise ExportError("Unknown export options; credentials must not be stored in a plan")
    at = _time(now)
    plan = {"schema_version": 1, "action": "export", "mode": "live", "destination": destination,
            "bundle_id": bundle.get("id"), "bundle_hash": _hash(bundle), "created_at": at.isoformat(),
            "expires_at": (at + timedelta(hours=24)).isoformat(), "options": options,
            "recipients": [], "operations": [], "excluded": [], "sending_enabled": False}
    if destination == "notion":
        data_source = _identifier(options.get("data_source_id"), "data_source_id")
        seen = set()
        for company in bundle.get("companies", []):
            if options.get("company_ids") is not None and company.get("id") not in options["company_ids"]:
                continue
            domain = str(company.get("domain") or "").strip().casefold()
            if not domain or domain in seen or company.get("identity_status") != "source_supported":
                plan["excluded"].append({"id": company.get("id"), "reason": "unsupported or duplicate identity"})
                continue
            seen.add(domain)
            props = {"Name": _rich(company.get("name"), "title"), "Domain": _rich(domain),
                     "Website": {"url": company.get("website") or None},
                     "Fit reason": _rich(company.get("fit_reason")),
                     "Segment": _rich(company.get("segment_id")), "Evidence": _rich(_sources(company))}
            plan["operations"].append({"id": "company-" + str(company["id"]), "kind": "notion_create",
                "domain": domain, "body": {"parent": {"type": "data_source_id", "data_source_id": data_source},
                                            "properties": props}})
    else:
        suppressed = {str(x).strip().casefold() for x in options.get("suppress_emails", [])}
        selected = options.get("contact_ids")
        sequence = None
        if destination == "instantly":
            _identifier(options.get("campaign_id"), "campaign_id")
            matches = [s for s in bundle.get("sequences", []) if s.get("id") == options.get("sequence_id")]
            if len(matches) != 1 or matches[0].get("channel") != "email":
                raise ExportError("Select exactly one email sequence_id for this campaign")
            sequence = matches[0]
            limit = options.get("daily_limit", 20)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                raise ExportError("daily_limit must be between 1 and 100")
            steps, previous = [], 0
            raw_steps = sequence.get("steps", [])
            for index, step in enumerate(sequence.get("steps", [])):
                day = step.get("day")
                if isinstance(day, bool) or not isinstance(day, int) or day < previous or (index == 0 and day != 0):
                    raise ExportError("Email sequence must start on day 0 and use ascending whole days")
                previous = day
            for index, step in enumerate(raw_steps):
                # Instantly delay belongs to the CURRENT step and precedes the NEXT email.
                delay = raw_steps[index + 1]["day"] - step["day"] if index + 1 < len(raw_steps) else 0
                steps.append({"type": "email", "delay": delay, "delay_unit": "days",
                              "variants": [{"subject": _subject_template(step.get("subject")),
                                "body": html.escape(str(step.get("body") or "")).replace("\n", "<br/>")}]})
            if not 1 <= len(steps) <= 10:
                raise ExportError("An email sequence needs 1–10 steps")
            plan["campaign_config"] = {"sequences": [{"steps": steps}], "stop_on_reply": True,
                "insert_unsubscribe_header": True, "allow_risky_contacts": False, "daily_limit": limit,
                "text_only": True}
            plan["operations"].append({"id": "campaign-config", "kind": "campaign_patch",
                                        "body": plan["campaign_config"]})
        seen = set()
        for company, contact in _contacts(bundle):
            if selected is not None and contact.get("id") not in selected:
                continue
            reason = _eligible(company, contact, suppressed, at)
            email = str(contact.get("email") or "").strip().casefold()
            if sequence and company.get("segment_id") != sequence.get("segment_id"):
                reason = "different campaign segment"
            if email in seen:
                reason = "duplicate recipient"
            if reason:
                plan["excluded"].append({"id": contact.get("id"), "reason": reason})
                continue
            seen.add(email)
            parts = str(contact.get("name") or "").split(maxsplit=1)
            recipient = {"contact_id": contact.get("id"), "company_id": company.get("id"), "email": email,
                         "email_status": contact["email_status"], "email_provider": contact["email_provider"],
                         "email_checked_at": contact["email_checked_at"], "do_not_contact": False}
            if destination == "hubspot":
                props = {"email": email, "firstname": parts[0] if parts else "",
                         "lastname": parts[1] if len(parts) > 1 else "", "company": company.get("name"),
                         "jobtitle": contact.get("role"), "website": company.get("website")}
                props = {k: v for k, v in props.items() if v}
                operation = {"id": "contact-" + str(contact["id"]), "kind": "hubspot_upsert",
                    "body": {"inputs": [{"id": email, "idProperty": "email", "properties": props}]}}
            else:
                variables = {"first_name": parts[0] if parts else "", "company_name": company.get("name"),
                             "product_name": bundle.get("product", {}).get("name"),
                             "fit_reason": company.get("fit_reason"), "sender_name": options.get("sender_name")}
                recipient["preview"] = [{"day": s["day"], "subject": _render(s["subject"], variables),
                                         "body": _render(s["body"], variables)} for s in sequence["steps"]]
                merge_values = {k: html.escape(str(v or "")) for k, v in variables.items()}
                merge_values.update({"obf_subject_" + k: str(v or "") for k, v in variables.items()})
                operation = {"id": "contact-" + str(contact["id"]), "kind": "instantly_create",
                    "body": {"campaign": options["campaign_id"], "email": email,
                             "first_name": parts[0] if parts else "", "company_name": company.get("name"),
                             "skip_if_in_workspace": True, "skip_if_in_campaign": True,
                             "custom_variables": merge_values}}
            plan["recipients"].append(recipient)
            plan["operations"].append(operation)
    if (destination != "notion" and not plan["recipients"]) or not plan["operations"]:
        raise ExportError("No eligible records remain; inspect identity, fit, verification, and suppression")
    if len(plan["recipients"]) > 1000 or len(plan["operations"]) > 1001:
        raise ExportError("Split exports into batches of at most 1,000 records")
    return _seal(plan)


def _write_json(path, value, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        return
    fd, temporary = tempfile.mkstemp(prefix=".export-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_export_plan(plan, path):
    _validate_hash(plan, plan.get("approval_hash"))
    _write_json(path, plan, exclusive=True)
    return Path(path)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_json(method, url, headers, payload):
    """One bounded HTTPS attempt. Credentials never follow redirects or enter errors."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc not in {urllib.parse.urlsplit(x).netloc for x in BASES.values()}:
        raise ExportError("Unexpected provider host")
    request = urllib.request.Request(url, method=method, headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=25) as response:
            raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ExportError("Provider response exceeds size limit")
            result = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raise ExportError("Provider HTTP " + str(error.code)) from None
    except (OSError, ValueError) as error:
        raise ExportError("Provider transport or JSON response failed") from None
    if not isinstance(result, dict) or result.get("error") or result.get("object") == "error":
        raise ExportError("Provider returned an error or unexpected response")
    return result


def _client(destination, token, transport):
    token = token or os.environ.get(ENV[destination])
    if not token or not isinstance(token, str) or "\n" in token or "\r" in token:
        raise ExportError("Set " + ENV[destination] + " explicitly before using this provider")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    if destination == "notion":
        headers["Notion-Version"] = "2025-09-03"
    send = transport or http_json
    def request(method, path, payload=None):
        return send(method, BASES[destination] + path, headers, payload)
    return request


def _load(path, approval, action, at):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    _validate_hash(plan, approval)
    if plan.get("mode") != "live" or plan.get("action") != action:
        raise ExportError("Wrong action or synthetic data")
    if _time(plan["expires_at"]) < at:
        raise ExportError("Plan expired; prepare and review a fresh plan")
    for recipient in plan.get("recipients", []):
        issue = _eligible({"identity_status": "source_supported", "fit_status": "potential_fit"},
                          recipient, set(), at)
        if issue:
            raise ExportError("Recipient no longer eligible: " + issue)
    return plan


@contextmanager
def _locked(path):
    lock = Path(str(path) + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ExportError("Operation already running or crashed; reconcile receipts before removing its lock") from None
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _receipt(path, plan):
    if path.exists():
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("approval_hash") != plan["approval_hash"]:
            raise ExportError("Receipt belongs to another plan")
        if any(row.get("status") in {"started", "unknown"} for row in receipt["operations"]):
            raise ExportError("Previous outcome is unknown; reconcile in provider before any retry")
        return receipt
    return {"approval_hash": plan["approval_hash"], "action": plan["action"],
            "status": "pending", "operations": []}


def _campaign(request, campaign_id):
    result = request("GET", "/api/v2/campaigns/" + campaign_id)
    if result.get("id") != campaign_id or type(result.get("status")) is not int or result["status"] not in {0, 2}:
        raise ExportError("Selected campaign must be DRAFT or PAUSED")
    return result


def _leads(request, campaign_id):
    rows, cursor, seen = [], None, set()
    for _ in range(11):
        body = {"campaign": campaign_id, "limit": 100}
        if cursor:
            body["starting_after"] = cursor
        response = request("POST", "/api/v2/leads/list", body)
        items = response.get("items")
        if not isinstance(items, list):
            raise ExportError("Malformed lead list; cannot reconcile campaign recipients")
        rows.extend(items)
        if len(rows) > 1000:
            raise ExportError("Campaign exceeds this workflow's 1,000-recipient bound")
        cursor = response.get("next_starting_after")
        if not cursor or not items:
            return rows
        if cursor in seen:
            raise ExportError("Provider repeated pagination cursor")
        seen.add(cursor)
    raise ExportError("Lead pagination incomplete")


def _sequence_view(sequences):
    """Ignore only documented default fields and server metadata in sequence read-back."""
    if not isinstance(sequences, list) or len(sequences) != 1:
        raise ExportError("Exactly one remote sequence is required")
    return [{"type": step.get("type"), "delay": step.get("delay"),
             "delay_unit": step.get("delay_unit", "days"),
             "variants": [{"subject": variant.get("subject"), "body": variant.get("body"),
                           "v_disabled": variant.get("v_disabled", False)} for variant in step.get("variants", [])]}
            for step in sequences[0].get("steps", [])]


def _matches_config(campaign, expected):
    return all((_sequence_view(campaign.get(key)) == _sequence_view(value)) if key == "sequences"
               else campaign.get(key) == value for key, value in expected.items())


def _check_remote_suppression(request, emails):
    """Read the workspace blocklist completely within a bounded 20-page budget."""
    cursor, seen = None, set()
    for _ in range(20):
        query = {"limit": 100}
        if cursor:
            query["starting_after"] = cursor
        response = request("GET", "/api/v2/block-lists-entries?" + urllib.parse.urlencode(query))
        items = response.get("items")
        if not isinstance(items, list):
            raise ExportError("Cannot establish current provider suppression list")
        for row in items:
            value = str(row.get("bl_value") or "").strip().casefold()
            if not value or type(row.get("is_domain")) is not bool:
                raise ExportError("Malformed provider blocklist entry")
            if (row["is_domain"] and any(email.rsplit("@", 1)[-1] == value for email in emails)) or value in emails:
                raise ExportError("Recipient or domain is suppressed in Instantly")
        cursor = response.get("next_starting_after")
        if not cursor or not items:
            return
        if cursor in seen:
            raise ExportError("Provider suppression pagination repeated")
        seen.add(cursor)
    raise ExportError("Suppression list exceeds the bounded read budget; export blocked")


def _perform(request, operation, plan):
    kind, body = operation["kind"], operation["body"]
    if kind == "hubspot_upsert":
        response = request("POST", "/crm/v3/objects/contacts/batch/upsert", body)
        results = response.get("results", [])
        if response.get("status") != "COMPLETE" or response.get("errors") or len(results) != 1 or not results[0].get("id"):
            raise ExportError("Contact upsert has no definitive completed record")
        return {"provider_id": results[0]["id"]}
    if kind == "notion_create":
        response = request("POST", "/v1/pages", body)
        if not response.get("id"):
            raise ExportError("Page creation has no definitive record ID")
        return {"provider_id": response["id"]}
    campaign_id = plan["options"]["campaign_id"]
    if kind == "campaign_patch":
        request("PATCH", "/api/v2/campaigns/" + campaign_id, body)
        current = _campaign(request, campaign_id)
        if not _matches_config(current, body):
            raise ExportError("Campaign configuration read-back differs from approved copy")
        return {"provider_id": campaign_id}
    if kind == "instantly_create":
        response = request("POST", "/api/v2/leads", body)
        if not response.get("id") or str(response.get("email") or "").casefold() != body["email"]:
            raise ExportError("Lead creation has no definitive recipient ID")
        return {"provider_id": response["id"], "email": body["email"]}
    raise ExportError("Unsupported operation")


def apply_export(plan_path, approval, *, token=None, transport=None, now=None, suppress_emails=None):
    """Apply one exact plan with durable receipts; never send or activate a campaign."""
    path = Path(plan_path)
    at = _time(now)
    plan = _load(path, approval, "export", at)
    suppressed = {str(e).strip().casefold() for e in suppress_emails or []}
    if any(r["email"] in suppressed for r in plan["recipients"]):
        raise ExportError("Recipient is now suppressed; prepare a new export plan")
    receipt_path = path.with_suffix(".receipt.json")
    with _locked(path):
        receipt = _receipt(receipt_path, plan)
        if receipt["status"] == "complete":
            return receipt
        request = _client(plan["destination"], token, transport)
        done = {r["id"]: r for r in receipt["operations"] if r["status"] in {"complete", "skipped_existing"}}
        if plan["destination"] == "instantly":
            _campaign(request, plan["options"]["campaign_id"])
            remote = _leads(request, plan["options"]["campaign_id"])
            imported = {r["provider_id"] for r in done.values() if r.get("email")}
            if {r.get("id") for r in remote} != imported:
                raise ExportError("Use an empty campaign; existing recipients are outside this approved plan")
            _check_remote_suppression(request, {r["email"] for r in plan["recipients"]})
        if plan["destination"] == "notion":
            schema = request("GET", "/v1/data_sources/" + plan["options"]["data_source_id"])
            if any(schema.get("properties", {}).get(name, {}).get("type") != kind
                   for name, kind in NOTION_PROPERTIES.items()):
                raise ExportError("Notion data source does not match the documented import schema")
        for operation in plan["operations"]:
            if operation["id"] in done:
                continue
            if plan["destination"] == "instantly":
                _campaign(request, plan["options"]["campaign_id"])
            if operation["kind"] == "notion_create":
                existing = request("POST", "/v1/data_sources/" + plan["options"]["data_source_id"] + "/query",
                    {"filter": {"property": "Domain", "rich_text": {"equals": operation["domain"]}}, "page_size": 2})
                if not isinstance(existing.get("results"), list):
                    raise ExportError("Malformed Notion duplicate check")
                if existing["results"]:
                    record = {"id": operation["id"], "status": "skipped_existing",
                              "provider_id": existing["results"][0]["id"]}
                    receipt["operations"].append(record)
                    _write_json(receipt_path, receipt)
                    continue
            record = {"id": operation["id"], "status": "started", "started_at": _time().isoformat()}
            receipt["operations"].append(record)
            receipt["status"] = "partial"
            _write_json(receipt_path, receipt)
            try:
                record.update(_perform(request, operation, plan))
            except Exception:
                record["status"] = "unknown"
                record["reason"] = "Write attempted; its remote outcome requires manual reconciliation"
                _write_json(receipt_path, receipt)
                raise ExportError("External write outcome unknown; reconcile in provider before retrying") from None
            record["status"] = "complete"
            record["completed_at"] = _time().isoformat()
            _write_json(receipt_path, receipt)
        receipt["status"] = "complete"
        receipt["completed_at"] = _time().isoformat()
        _write_json(receipt_path, receipt)
        return receipt


def _activation_snapshot(plan, request):
    campaign_id = plan["options"]["campaign_id"]
    campaign = _campaign(request, campaign_id)
    expected = plan["campaign_config"]
    if not _matches_config(campaign, expected):
        raise ExportError("Campaign copy or sending controls changed from approved export")
    if campaign.get("stop_on_reply") is not True or campaign.get("insert_unsubscribe_header") is not True:
        raise ExportError("Reply stopping and unsubscribe headers must be enabled")
    senders = campaign.get("email_list")
    schedule = campaign.get("campaign_schedule")
    if not isinstance(senders, list) or not senders or not isinstance(schedule, dict) or not schedule.get("schedules"):
        raise ExportError("Configure sender accounts and a sending schedule in Instantly first")
    accounts = []
    for sender in sorted(set(senders)):
        account = request("GET", "/api/v2/accounts/" + urllib.parse.quote(sender, safe=""))
        if account.get("email") != sender or type(account.get("status")) is not int or account["status"] != 1:
            raise ExportError("A selected sender account is not active")
        if isinstance(account.get("warmup_status"), int) and account["warmup_status"] < 0:
            raise ExportError("A selected sender has unhealthy warmup status")
        accounts.append({"email": sender, "status": account["status"], "warmup_status": account.get("warmup_status")})
    vitals = request("POST", "/api/v2/accounts/test/vitals", {"accounts": sorted(set(senders))})
    good_domains = {row.get("domain") for row in vitals.get("success_list", [])
                    if row.get("allPass") is True and not row.get("indeterminate")}
    domains = {sender.rsplit("@", 1)[-1] for sender in senders}
    if vitals.get("status") != "success" or vitals.get("failure_list") or not domains <= good_domains:
        raise ExportError("Sender domain vitals are unhealthy or inconclusive")
    leads = _leads(request, campaign_id)
    expected_by_email = {op["body"]["email"]: op["body"] for op in plan["operations"] if op["kind"] == "instantly_create"}
    actual_by_email = {str(lead.get("email") or "").casefold(): lead for lead in leads}
    if len(actual_by_email) != len(leads) or set(actual_by_email) != set(expected_by_email):
        raise ExportError("Remote recipients differ from the exact approved and verified recipient set")
    _check_remote_suppression(request, set(expected_by_email))
    normalized = []
    for email, expected_lead in sorted(expected_by_email.items()):
        lead = actual_by_email[email]
        payload = lead.get("payload")
        if not isinstance(payload, dict) or any(payload.get(k) != v for k, v in expected_lead["custom_variables"].items()):
            raise ExportError("Remote personalization differs from reviewed recipient copy")
        if not lead.get("id") or lead.get("campaign") != campaign_id:
            raise ExportError("Lead is not attached to the selected campaign")
        if type(lead.get("status")) is not int or lead["status"] != 1:
            raise ExportError("Recipient is no longer an uncontacted active lead")
        if lead.get("timestamp_last_contact") or lead.get("email_reply_count", 0) != 0 or lead.get("lt_interest_status") is not None:
            raise ExportError("Recipient was already contacted, replied, or has an interest disposition")
        if lead.get("verification_status") not in {None, 0, 1}:
            raise ExportError("Remote verification is pending, risky, or invalid")
        normalized.append({"id": lead["id"], "email": email, "status": lead["status"],
                           "payload": {k: payload[k] for k in expected_lead["custom_variables"]}})
    keys = ["id", "name", "status", "sequences", "email_list", "campaign_schedule", "daily_limit",
            "daily_max_leads", "stop_on_reply", "stop_on_auto_reply", "stop_for_company",
            "insert_unsubscribe_header", "allow_risky_contacts", "text_only"]
    return {"campaign": {key: campaign.get(key) for key in keys}, "accounts": accounts,
            "sender_domains_passed": sorted(good_domains), "leads": normalized}


def prepare_activation(export_plan_path, *, token=None, transport=None, now=None):
    """Read-only preflight: bind current recipients, copy, schedule and sender accounts."""
    path = Path(export_plan_path)
    original = json.loads(path.read_text(encoding="utf-8"))
    at = _time(now)
    original = _load(path, original.get("approval_hash"), "export", at)
    if original["destination"] != "instantly":
        raise ExportError("Only Instantly campaigns can be activated")
    receipt_path = path.with_suffix(".receipt.json")
    if not receipt_path.exists():
        raise ExportError("Apply and reconcile the approved import first")
    receipt = _receipt(receipt_path, original)
    if receipt["status"] != "complete":
        raise ExportError("Import receipts must all be complete before activation")
    if {row["id"] for row in receipt["operations"] if row.get("status") == "complete"} != {
            operation["id"] for operation in original["operations"]}:
        raise ExportError("Import receipt does not reconcile every approved operation")
    request = _client("instantly", token, transport)
    snapshot = _activation_snapshot(original, request)
    imported_ids = {row["provider_id"] for row in receipt["operations"] if row.get("email")}
    if imported_ids != {row["id"] for row in snapshot["leads"]}:
        raise ExportError("Remote recipient IDs differ from the completed import receipts")
    plan = {"schema_version": 1, "action": "activate", "mode": "live", "destination": "instantly",
            "created_at": at.isoformat(), "expires_at": (at + timedelta(minutes=30)).isoformat(),
            "sending_enabled": True, "recipients": original["recipients"],
            "import_plan": original, "import_receipt_hash": _hash(receipt),
            "snapshot": snapshot, "snapshot_hash": _hash(snapshot)}
    return _seal(plan)


def apply_activation(plan_path, approval, *, token=None, transport=None, now=None, suppress_emails=None):
    """Activate exactly the reviewed campaign. This operation can send real email."""
    path = Path(plan_path)
    plan = _load(path, approval, "activate", _time(now))
    if plan.get("sending_enabled") is not True:
        raise ExportError("Activation plan must explicitly enable sending")
    suppressed = {str(e).strip().casefold() for e in suppress_emails or []}
    if any(r["email"] in suppressed for r in plan["recipients"]):
        raise ExportError("Recipient is now suppressed; prepare a new plan")
    receipt_path = path.with_suffix(".receipt.json")
    with _locked(path):
        receipt = _receipt(receipt_path, plan)
        if receipt["status"] == "complete":
            return receipt
        request = _client("instantly", token, transport)
        current = _activation_snapshot(plan["import_plan"], request)
        if _hash(current) != plan["snapshot_hash"]:
            raise ExportError("Campaign snapshot changed; prepare a new activation approval")
        record = {"id": "activate", "status": "started", "started_at": _time().isoformat()}
        receipt["operations"] = [record]
        receipt["status"] = "partial"
        _write_json(receipt_path, receipt)
        campaign_id = current["campaign"]["id"]
        try:
            request("POST", "/api/v2/campaigns/" + campaign_id + "/activate", {})
            result = request("GET", "/api/v2/campaigns/" + campaign_id)
            if result.get("id") != campaign_id or type(result.get("status")) is not int or result["status"] != 1:
                raise ExportError("Campaign activation not confirmed by read-back")
        except Exception:
            record["status"] = "unknown"
            record["reason"] = "Activation attempted; check provider status before any retry"
            _write_json(receipt_path, receipt)
            raise ExportError("Activation outcome unknown; reconcile in provider before retrying") from None
        record.update(status="complete", provider_id=campaign_id, completed_at=_time().isoformat())
        receipt["status"] = "complete"
        receipt["note"] = "Campaign active; this confirms activation, not delivery or replies"
        _write_json(receipt_path, receipt)
        return receipt
