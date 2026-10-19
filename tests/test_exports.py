"""Export/activation contracts exercised through the real HTTP boundary seam."""
import copy
import csv
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from buyerfind.exports import (ExportError, export_csv, prepare_export,
                               save_export_plan, apply_export,
                               prepare_activation, apply_activation)


def bundle():
    return {"schema_version": 2, "id": "live-test", "mode": "live",
            "status": "partial", "product": {"name": "Test Product"},
            "companies": [{"id": "c1", "name": "Acme", "domain": "acme.test",
                           "website": "https://acme.test", "segment_id": "s1",
                           "fit_status": "potential_fit", "identity_status": "source_supported",
                           "fit_reason": "You maintain a write workflow.",
                           "evidence": [{"source_url": "https://acme.test", "quote": "workflow"}],
                           "contacts": [{"id": "p1", "name": "Ada Tester", "role": "CTO",
                                         "email": "ada@acme.test", "email_status": "valid",
                                         "email_provider": "provider-test",
                                         "email_checked_at": datetime.now(timezone.utc).isoformat(),
                                         "do_not_contact": False}]}],
            "sequences": [{"id": "seq1", "segment_id": "s1", "name": "Discovery", "channel": "email",
                           "status": "draft", "steps": [{"day": 0, "subject": "Question for {{first_name}}",
                           "body": "Hi {{first_name}}, {{fit_reason}} Could {{product_name}} help? {{sender_name}}"}]}],
            "competitors": [], "segments": []}


class Transport:
    """Stateful fake service, not mocks of export internals."""
    def __init__(self):
        self.calls = []
        self.leads = []
        self.blocklist = []
        self.campaign = {"id": "campaign-1", "status": 0, "name": "Trial",
                         "email_list": ["sender@founder.test"],
                         "campaign_schedule": {"schedules": [{"name": "Business hours",
                           "timing": {"from": "09:00", "to": "17:00"},
                           "days": {"1": True}, "timezone": "Asia/Karachi"}]}}
        self.fail_write = False

    def __call__(self, method, url, headers, payload):
        self.calls.append((method, url, copy.deepcopy(payload)))
        if url.endswith("/contacts/batch/upsert"):
            if self.fail_write:
                raise TimeoutError("ambiguous write")
            return {"status": "COMPLETE", "results": [{"id": "contact-123"}]}
        if "/data_sources/" in url and method == "GET":
            return {"properties": {"Name": {"type": "title"}, "Domain": {"type": "rich_text"},
                    "Website": {"type": "url"}, "Fit reason": {"type": "rich_text"},
                    "Segment": {"type": "rich_text"}, "Evidence": {"type": "rich_text"}}}
        if url.endswith("/query"):
            return {"results": [], "has_more": False}
        if url.endswith("/pages"):
            return {"id": "page-1"}
        if url.endswith("/leads/list"):
            return {"items": copy.deepcopy(self.leads), "next_starting_after": None}
        if "/block-lists-entries?" in url:
            return {"items": copy.deepcopy(self.blocklist), "next_starting_after": None}
        if url.endswith("/leads") and method == "POST":
            lead = {"id": "lead-" + str(len(self.leads) + 1), "email": payload["email"],
                    "campaign": payload["campaign"], "status": 1,
                    "payload": copy.deepcopy(payload["custom_variables"])}
            self.leads.append(lead)
            if self.fail_write:
                raise TimeoutError("write might have succeeded")
            return copy.deepcopy(lead)
        if url.endswith("/accounts/test/vitals"):
            return {"status": "success", "success_list": [{"domain": "founder.test", "allPass": True}],
                    "failure_list": []}
        if "/accounts/" in url:
            return {"email": "sender@founder.test", "status": 1, "warmup_status": 1}
        if url.endswith("/activate"):
            self.campaign["status"] = 1
            if self.fail_write:
                raise TimeoutError("activation might have succeeded")
            return copy.deepcopy(self.campaign)
        if "/campaigns/" in url:
            if method == "PATCH":
                self.campaign.update(copy.deepcopy(payload))
            return copy.deepcopy(self.campaign)
        raise AssertionError((method, url, payload))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def plan(self, destination="instantly", value=None):
        options = {"campaign_id": "campaign-1", "sequence_id": "seq1", "sender_name": "Hifza"}
        if destination == "notion":
            options = {"data_source_id": "source-1"}
        if destination == "hubspot":
            options = {}
        return prepare_export(value or bundle(), destination, options)

    def write_plan(self, plan, name="plan.json"):
        p = self.root / name
        save_export_plan(plan, p)
        return p

    def test_csv_preserves_status_and_neutralizes_formulas(self):
        value = bundle()
        value["companies"][0]["name"] = " \t=HYPERLINK(\"bad\")"
        table = list(csv.DictReader(io.StringIO(export_csv(value))))
        self.assertTrue(table[0]["name"].startswith("'"))
        self.assertEqual(table[0]["mode"], "live")
        contact = list(csv.DictReader(io.StringIO(export_csv(value, "contacts"))))[0]
        self.assertEqual(contact["email_status"], "valid")
        for kind in ["sequences", "competitors", "triggers", "hubspot", "notion", "instantly"]:
            self.assertTrue(export_csv(value, kind).strip())

    def test_demo_invalid_stale_suppressed_and_duplicate_recipients_blocked(self):
        value = bundle()
        value["mode"] = "synthetic_demo"
        with self.assertRaisesRegex(ExportError, "synthetic"):
            self.plan(value=value)
        for field, bad in [("email_status", "accept_all"), ("email_status", "published_unverified"),
                           ("do_not_contact", True), ("email_provider", ""),
                           ("email_checked_at", "2020-01-01T00:00:00+00:00")]:
            value = bundle()
            value["companies"][0]["contacts"][0][field] = bad
            with self.assertRaisesRegex(ExportError, "eligible"):
                self.plan(value=value)
        value = bundle()
        duplicate = copy.deepcopy(value["companies"][0]["contacts"][0])
        duplicate["id"] = "duplicate"
        value["companies"][0]["contacts"].append(duplicate)
        self.assertEqual(len(self.plan(value=value)["recipients"]), 1)

    def test_missing_or_unsupported_personalization_fails_before_network(self):
        for text in ["Hi {{unknown}}", "Hi {{first_name}", "Hi {{sender_name}}"]:
            value = bundle()
            value["sequences"][0]["steps"][0]["body"] = text
            options = {"campaign_id": "campaign-1", "sequence_id": "seq1", "sender_name": ""}
            with self.assertRaises(ExportError):
                prepare_export(value, "instantly", options)

    def test_followup_delays_map_to_next_email_and_body_is_html_escaped(self):
        value = bundle()
        sequence = value["sequences"][0]
        sequence["steps"][0]["body"] = "Hi {{first_name}},\nA < B"
        sequence["steps"].extend([{"day": 3, "subject": "Follow up", "body": "Any thoughts?"},
                                   {"day": 8, "subject": "Last note", "body": "Thanks"}])
        plan = self.plan(value=value)
        steps = plan["campaign_config"]["sequences"][0]["steps"]
        self.assertEqual([s["delay"] for s in steps], [3, 5, 0])
        self.assertIn("<br/>", steps[0]["variants"][0]["body"])
        self.assertIn("&lt;", steps[0]["variants"][0]["body"])

    def test_subject_personalization_preserves_plain_text_while_body_values_are_escaped(self):
        value = bundle()
        value["companies"][0]["name"] = "A&B <Tools>"
        value["sequences"][0]["steps"][0]["subject"] = "For {{company_name}}"
        plan = self.plan(value=value)
        remote = plan["operations"][-1]["body"]["custom_variables"]
        self.assertEqual(remote["obf_subject_company_name"], "A&B <Tools>")
        self.assertEqual(remote["company_name"], "A&amp;B &lt;Tools&gt;")
        self.assertEqual(plan["recipients"][0]["preview"][0]["subject"], "For A&B <Tools>")
        subject = plan["campaign_config"]["sequences"][0]["steps"][0]["variants"][0]["subject"]
        self.assertEqual(subject, "For {{obf_subject_company_name}}")

    def test_hash_approval_detects_tampering_and_never_networks(self):
        plan = self.plan("hubspot")
        path = self.write_plan(plan)
        transport = Transport()
        with self.assertRaises(ExportError):
            apply_export(path, "yes", token="test", transport=transport)
        changed = json.loads(path.read_text())
        changed["operations"][0]["body"]["inputs"][0]["properties"]["firstname"] = "Changed"
        path.write_text(json.dumps(changed))
        with self.assertRaises(ExportError):
            apply_export(path, plan["approval_hash"], token="test", transport=transport)
        self.assertEqual(transport.calls, [])

    def test_expired_plan_and_new_local_suppression_block_before_network(self):
        plan = self.plan("hubspot")
        path = self.write_plan(plan)
        service = Transport()
        with self.assertRaisesRegex(ExportError, "expired"):
            apply_export(path, plan["approval_hash"], token="test", transport=service,
                         now=datetime.now(timezone.utc) + timedelta(hours=25))
        with self.assertRaisesRegex(ExportError, "suppressed"):
            apply_export(path, plan["approval_hash"], token="test", transport=service,
                         suppress_emails=["ADA@ACME.TEST"])
        self.assertEqual(service.calls, [])

    def test_hubspot_upserts_by_email_and_receipts_prevent_repeat(self):
        plan = self.plan("hubspot")
        path = self.write_plan(plan)
        service = Transport()
        first = apply_export(path, plan["approval_hash"], token="test", transport=service)
        self.assertEqual(first["status"], "complete")
        data = service.calls[0][2]["inputs"][0]
        self.assertEqual(data["idProperty"], "email")
        self.assertEqual(data["id"], "ada@acme.test")
        self.assertNotIn("lifecyclestage", data["properties"])
        self.assertEqual(apply_export(path, plan["approval_hash"], token="test", transport=service), first)
        self.assertEqual(len(service.calls), 1)

    def test_ambiguous_writes_have_durable_unknown_receipt_and_no_retry(self):
        plan = self.plan("hubspot")
        path = self.write_plan(plan)
        service = Transport()
        service.fail_write = True
        with self.assertRaisesRegex(ExportError, "unknown"):
            apply_export(path, plan["approval_hash"], token="test", transport=service)
        before = len(service.calls)
        with self.assertRaisesRegex(ExportError, "reconcile"):
            apply_export(path, plan["approval_hash"], token="test", transport=service)
        self.assertEqual(len(service.calls), before)
        receipt = json.loads(path.with_suffix(".receipt.json").read_text())
        self.assertEqual(receipt["operations"][0]["status"], "unknown")
        self.assertNotIn("test", path.with_suffix(".receipt.json").read_text())

    def test_notion_uses_data_source_query_and_schema(self):
        plan = self.plan("notion")
        path = self.write_plan(plan)
        service = Transport()
        apply_export(path, plan["approval_hash"], token="test", transport=service)
        self.assertEqual(service.calls[-1][2]["parent"],
                         {"type": "data_source_id", "data_source_id": "source-1"})
        self.assertTrue(service.calls[-2][1].endswith("/data_sources/source-1/query"))

    def test_notion_existing_domain_is_preserved_without_write(self):
        plan = self.plan("notion")
        path = self.write_plan(plan)
        service = Transport()
        def transport(method, url, headers, payload):
            if url.endswith("/query"):
                service.calls.append((method, url, payload))
                return {"results": [{"id": "existing-page"}], "has_more": False}
            return service(method, url, headers, payload)
        receipt = apply_export(path, plan["approval_hash"], token="test", transport=transport)
        self.assertEqual(receipt["operations"][0]["status"], "skipped_existing")
        self.assertFalse(any(url.endswith("/pages") for _, url, _ in service.calls))

    def test_instantly_import_never_activates_and_rejects_active_or_nonempty(self):
        for status, leads in [(1, []), (0, [{"id": "old", "email": "other@acme.test"}])]:
            plan = self.plan()
            path = self.write_plan(plan, "blocked-" + str(status) + ".json")
            service = Transport()
            service.campaign["status"] = status
            service.leads = leads
            with self.assertRaises(ExportError):
                apply_export(path, plan["approval_hash"], token="test", transport=service)
            self.assertFalse(any(method == "PATCH" or url.endswith("/leads")
                                 for method, url, _ in service.calls))
        plan = self.plan()
        path = self.write_plan(plan)
        service = Transport()
        receipt = apply_export(path, plan["approval_hash"], token="test", transport=service)
        self.assertEqual(receipt["status"], "complete")
        self.assertEqual(service.campaign["status"], 0)
        self.assertFalse(any(url.endswith("/activate") for _, url, _ in service.calls))

    def test_activation_requires_same_recipients_copy_accounts_and_explicit_hash(self):
        plan = self.plan()
        path = self.write_plan(plan)
        service = Transport()
        apply_export(path, plan["approval_hash"], token="test", transport=service)
        activation = prepare_activation(path, token="test", transport=service)
        self.assertEqual(service.campaign["status"], 0)
        activation_path = self.write_plan(activation, "activate.json")
        before = len(service.calls)
        with self.assertRaises(ExportError):
            apply_activation(activation_path, "yes", token="test", transport=service)
        self.assertEqual(before, len(service.calls))
        service.campaign["daily_limit"] = 999
        with self.assertRaisesRegex(ExportError, "changed"):
            apply_activation(activation_path, activation["approval_hash"], token="test", transport=service)
        service.campaign["daily_limit"] = 20
        result = apply_activation(activation_path, activation["approval_hash"], token="test", transport=service)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(service.campaign["status"], 1)
        calls = len(service.calls)
        apply_activation(activation_path, activation["approval_hash"], token="test", transport=service)
        self.assertEqual(calls, len(service.calls))

    def test_activation_rejects_added_lead_changed_copy_missing_unsubscribe(self):
        plan = self.plan()
        path = self.write_plan(plan)
        service = Transport()
        apply_export(path, plan["approval_hash"], token="test", transport=service)
        original = copy.deepcopy(service.campaign)
        service.leads.append({"id": "outsider", "email": "other@acme.test"})
        with self.assertRaises(ExportError):
            prepare_activation(path, token="test", transport=service)
        service.leads.pop()
        service.campaign["sequences"][0]["steps"][0]["variants"][0]["body"] = "Different message"
        with self.assertRaises(ExportError):
            prepare_activation(path, token="test", transport=service)
        service.campaign = original
        service.campaign["insert_unsubscribe_header"] = False
        with self.assertRaises(ExportError):
            prepare_activation(path, token="test", transport=service)

    def test_current_remote_suppression_and_ambiguous_activation_are_fail_closed(self):
        plan = self.plan()
        path = self.write_plan(plan)
        service = Transport()
        service.blocklist = [{"bl_value": "acme.test", "is_domain": True}]
        with self.assertRaisesRegex(ExportError, "suppressed"):
            apply_export(path, plan["approval_hash"], token="test", transport=service)
        self.assertFalse(any(method == "PATCH" for method, _, _ in service.calls))
        service.blocklist = []
        apply_export(path, plan["approval_hash"], token="test", transport=service)
        activation = prepare_activation(path, token="test", transport=service)
        activation_path = self.write_plan(activation, "activate.json")
        service.blocklist = [{"bl_value": "ada@acme.test", "is_domain": False}]
        with self.assertRaisesRegex(ExportError, "suppressed"):
            apply_activation(activation_path, activation["approval_hash"], token="test", transport=service)
        service.blocklist = []
        service.fail_write = True
        with self.assertRaisesRegex(ExportError, "unknown"):
            apply_activation(activation_path, activation["approval_hash"], token="test", transport=service)
        calls = len(service.calls)
        with self.assertRaisesRegex(ExportError, "reconcile"):
            apply_activation(activation_path, activation["approval_hash"], token="test", transport=service)
        self.assertEqual(len(service.calls), calls)

    def test_reply_or_failed_sender_vitals_block_activation(self):
        plan = self.plan()
        path = self.write_plan(plan)
        service = Transport()
        apply_export(path, plan["approval_hash"], token="test", transport=service)
        service.leads[0]["email_reply_count"] = 1
        with self.assertRaisesRegex(ExportError, "replied"):
            prepare_activation(path, token="test", transport=service)
        service.leads[0]["email_reply_count"] = 0
        def transport(method, url, headers, payload):
            if url.endswith("/accounts/test/vitals"):
                return {"status": "success", "success_list": [],
                        "failure_list": [{"domain": "founder.test", "allPass": False}]}
            return service(method, url, headers, payload)
        with self.assertRaisesRegex(ExportError, "vitals"):
            prepare_activation(path, token="test", transport=transport)
        self.assertFalse(any(url.endswith("/activate") for _, url, _ in service.calls))


if __name__ == "__main__":
    unittest.main()
