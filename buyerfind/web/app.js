"use strict";

// Source material is data. Dynamic content is always inserted as text nodes.
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="studio-token"]').content;
const app = {bundle: null, run: "", view: "overview", busy: false, refresh: null};
const array = value => Array.isArray(value) ? value : [];
const text = value => value == null ? "" : String(value);
const words = value => text(value).replaceAll("_", " ");
const number = value => Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : 0;
const el = (tag, className = "", value = null) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value != null) node.textContent = text(value);
  return node;
};
function append(parent, ...children) { children.filter(Boolean).forEach(child => parent.append(child)); return parent; }
function set(id, value) { $(id).textContent = text(value); }
function safeLink(value, label) {
  try {
    const url = new URL(value);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) throw new Error("unsupported");
    const a = el("a", "", label || url.hostname);
    a.href = url.href; a.target = "_blank"; a.rel = "noopener noreferrer";
    return a;
  } catch (_) { return el("span", "", label || "Source unavailable"); }
}
function formatDate(value) {
  if (!value) return "Date not established";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date not established" : date.toLocaleDateString(undefined, {day: "numeric", month: "short", year: "numeric"});
}
function notice(message = "") { set("notice", message); $("notice").hidden = !message; }
async function request(path, data) {
  const options = data === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-Studio-Token": token}, body: JSON.stringify(data)};
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The local research request could not finish.");
  return result;
}
function tag(label, status = "") {
  const positive = ["valid", "source_supported", "potential_fit", "dated_signal"];
  const warning = ["unknown", "needs_context", "accept_all", "published_unverified", "undated_signal"];
  const danger = ["invalid", "not_fit", "stale_signal"];
  return el("span", "tag " + (positive.includes(status) ? "positive" : warning.includes(status) ? "warning" : danger.includes(status) ? "danger" : ""), label);
}
function evidenceButton(title, record, company = null) {
  const button = el("button", "evidence-button", "View evidence ↗");
  button.type = "button"; button.addEventListener("click", () => showEvidence(title, record, company));
  return button;
}
function evidenceEntries(parent, title, items) {
  if (!array(items).length) return;
  parent.append(el("h3", "evidence-group", title));
  array(items).forEach(item => {
    const card = el("div", "evidence-entry");
    append(card, safeLink(item.source_url, item.source_url || "Source unavailable"),
      el("blockquote", "", item.quote || "No excerpt supplied"),
      el("time", "", "Checked " + formatDate(item.checked_at)));
    parent.append(card);
  });
}
function showEvidence(title, record, company) {
  set("evidence-title", title);
  const host = $("evidence-content"); host.replaceChildren();
  const reason = record.fit_reason || record.reason || record.summary || record.role;
  if (reason) host.append(el("p", "dialog-copy", reason));
  if (record.identity_status) host.append(tag("Company identity: " + words(record.identity_status), record.identity_status));
  if (record.email) {
    const paragraph = el("p", "dialog-copy", record.email + " · " + words(record.email_status || "unknown"));
    if (record.email_checked_at) paragraph.append(document.createTextNode(" · Checked " + formatDate(record.email_checked_at)));
    host.append(paragraph);
  }
  evidenceEntries(host, "Source excerpts", record.evidence);
  array(record.tech_stack).forEach(tech => evidenceEntries(host, "Technology · " + tech.name, tech.evidence));
  array(record.triggers).forEach(trigger => evidenceEntries(host, words(trigger.kind) + " · " + trigger.summary, trigger.evidence));
  array(record.review_gaps).forEach(gap => {
    host.append(el("h3", "evidence-group", "Review observation"));
    host.append(el("p", "dialog-copy", gap.complaint));
    if (gap.marketing_angle) host.append(el("p", "dialog-copy", "Possible angle: " + gap.marketing_angle));
    evidenceEntries(host, "Review evidence", gap.evidence);
  });
  if (company && !array(record.evidence).length) evidenceEntries(host, "Company context (does not verify this contact)", company.evidence);
  if (!host.children.length) host.append(el("p", "dialog-copy", "No source evidence has been recorded for this item."));
  host.append(el("p", "dialog-copy", "Source support does not establish buying intent, budget, decision authority, or permission to contact."));
  $("evidence-dialog").showModal();
}
function segmentName(id) { return array(app.bundle?.segments).find(row => row.id === id)?.name || "Unassigned"; }
function allPeople() {
  const people = array(app.bundle?.companies).flatMap(company => array(company.contacts).map(contact => ({...contact, company})));
  const shortlist = app.bundle?.people_shortlist;
  if (!Array.isArray(shortlist)) return people;
  const chosen = new Set(shortlist);
  return people.filter(person => chosen.has(person.id));
}
function filters(rows, getSegment) {
  const query = $("table-search").value.toLocaleLowerCase().trim();
  const segment = $("segment-filter").value;
  return rows.filter(row => (!segment || !getSegment || getSegment(row) === segment) &&
    (!query || JSON.stringify(row).toLocaleLowerCase().includes(query)));
}
function empty(message) { return el("div", "table-empty", message); }
function table(headers, rows, cells) {
  if (!rows.length) return empty("No matching records yet. Try another filter or inspect the run's research notes.");
  const wrapper = el("div", "table-wrap"), table = el("table"), head = el("thead"), tr = el("tr"), body = el("tbody");
  headers.forEach(label => tr.append(el("th", "", label))); head.append(tr);
  rows.forEach(row => { const tr = el("tr"); cells(row).forEach(content => { const td = el("td"); td.append(content); tr.append(td); }); body.append(tr); });
  append(table, head, body); wrapper.append(table); return wrapper;
}
function labelled(primary, secondary) { return append(el("div"), el("strong", "", primary || "Unknown"), secondary ? el("span", "sub", secondary) : null); }
function sectionHeading(name, aside = "") { return append(el("div", "section-heading"), el("h3", "", name), el("span", "muted", aside)); }
function renderOverview(host) {
  const segments = array(app.bundle.segments);
  host.append(sectionHeading("Who this product could help", `${segments.length} / ${app.bundle.targets?.segments || 6} outreach segments`));
  if (!segments.length) host.append(empty("Segments appear after the product and customer profile are researched."));
  const grid = el("div", "segment-grid");
  segments.forEach((segment, index) => {
    const card = el("article", "segment-card");
    append(card, el("span", "segment-index", "SEGMENT " + String(index + 1).padStart(2, "0")), el("h3", "", segment.name),
      el("p", "", segment.pain), el("div", "role", segment.buyer_role || "Buyer role not established"));
    if (segment.angle) card.append(el("p", "", "Angle: " + segment.angle));
    grid.append(card);
  }); host.append(grid);
  const bottom = el("div", "overview-bottom"), coverage = el("section", "panel"), product = el("section", "panel");
  coverage.append(el("h3", "", "What still needs research"));
  const gaps = array(app.bundle.summary?.shortfalls);
  if (gaps.length) { const ul = el("ul"); gaps.forEach(gap => ul.append(el("li", "", gap))); coverage.append(ul); }
  else coverage.append(el("p", "", app.bundle.mode === "synthetic_demo" ? "This fictional pack demonstrates the workflow. It cannot establish real target completion." : "Check provider notes and inspect the evidence before outreach. Reaching a count does not validate buyer intent."));
  product.append(el("h3", "", "Product boundaries"));
  const exclusions = array(app.bundle.product?.exclusions);
  if (exclusions.length) { const ul = el("ul"); exclusions.forEach(item => ul.append(el("li", "", item))); product.append(ul); }
  else product.append(el("p", "", "Product exclusions have not been established in this run."));
  append(bottom, coverage, product); host.append(bottom);
  const usage = app.bundle.usage || {};
  if (Object.keys(usage).length) {
    const panel = el("section", "panel"); panel.append(el("h3", "", "Usage recorded for this run"));
    const tags = el("div", "tag-list");
    Object.entries(usage).filter(([, value]) => typeof value === "number").forEach(([key, value]) => tags.append(tag(words(key) + ": " + value)));
    panel.append(tags); panel.append(el("p", "", "Request counts are not a currency estimate. Your provider account determines charges."));
    panel.style.marginTop = "15px"; host.append(panel);
  }
}
function renderCompanies(host) {
  const rows = filters(array(app.bundle.companies), row => row.segment_id);
  set("view-count", rows.length + " companies");
  host.append(table(["COMPANY", "SEGMENT", "FIT ASSESSMENT", "CONTACT COVERAGE", "EVIDENCE"], rows, company => {
    const identity = labelled(company.name, company.domain);
    identity.append(safeLink(company.website, "Website ↗"));
    const contacts = array(company.contacts);
    const valid = contacts.filter(person => person.email_status === "valid" && !person.do_not_contact).length;
    return [identity, el("span", "", segmentName(company.segment_id)),
      append(el("div"), tag(words(company.fit_status || "needs_context"), company.fit_status), el("span", "sub", words(company.identity_status || "unknown") + " identity")),
      labelled(contacts.length + (contacts.length === 1 ? " contact" : " contacts"), valid + " with provider-valid email; freshness in evidence"), evidenceButton(company.name, company)];
  }));
}
function renderPeople(host) {
  const rows = filters(allPeople(), row => row.company.segment_id);
  set("view-count", rows.length + " shortlisted people");
  host.append(table(["PERSON", "COMPANY", "EMAIL STATUS", "PUBLIC PROFILE", "EVIDENCE"], rows, person => {
    const email = labelled(person.email || "No email found", words(person.email_status || "missing"));
    if (person.do_not_contact) email.append(tag("Do not contact", "invalid"));
    return [labelled(person.name || "Name not established", person.role), labelled(person.company.name, segmentName(person.company.segment_id)), email,
      person.linkedin ? safeLink(person.linkedin, "LinkedIn ↗") : el("span", "muted", "Not found"), evidenceButton(person.name || "Contact evidence", person, person.company)];
  }));
}
function renderCompetitors(host) {
  const rows = filters(array(app.bundle.competitors)); set("view-count", rows.length + " competitors");
  if (!rows.length) return host.append(empty("No competitor records match this view."));
  const grid = el("div", "cards-grid");
  rows.forEach(row => {
    const card = el("article", "research-card"), heading = el("div", "card-head"), title = el("div");
    append(title, el("h3", "", row.name), safeLink(row.website, "Website ↗"));
    append(heading, title, tag(words(row.relationship))); card.append(heading);
    card.append(el("p", "", row.reason));
    card.append(append(el("div", "pricing"), el("small", "", "PRICING OBSERVED"), el("span", "", row.pricing || "Pricing not established")));
    const features = el("div", "tag-list"); array(row.features).forEach(feature => features.append(tag(feature))); card.append(features);
    array(row.review_gaps).forEach(gap => {
      const details = el("details");
      append(details, el("summary", "", "Review observation"), el("p", "", gap.complaint), el("p", "", "Possible marketing angle: " + (gap.marketing_angle || "Not yet assessed")));
      card.append(details);
    });
    const button = evidenceButton(row.name, row); button.style.marginTop = "15px"; card.append(button); grid.append(card);
  }); host.append(grid);
}
function renderCampaigns(host) {
  const rows = filters(array(app.bundle.sequences), row => row.segment_id); set("view-count", rows.length + " draft sequences");
  host.append(el("p", "dialog-copy", "Review copy, evidence, and personalization before use. These are draft sequences; conversion performance has not been measured. LinkedIn output is a manual task queue."));
  if (!rows.length) return host.append(empty("Campaign drafts appear after segments and product context are available."));
  const grid = el("div", "cards-grid");
  rows.forEach(sequence => {
    const card = el("article", "research-card"), title = el("div");
    append(title, el("h3", "", sequence.name), el("span", "sub", segmentName(sequence.segment_id)));
    card.append(append(el("div", "card-head"), title, tag(words(sequence.channel) + " · draft")));
    array(sequence.steps).forEach((step, index) => {
      const content = el("div", "sequence-step");
      append(content, el("div", "step-day", "DAY " + number(step.day) + " · " + (index ? "FOLLOW-UP" : "OPENING")),
        step.subject ? el("div", "step-subject", step.subject) : null, el("p", "step-body", step.body));
      if (index) { const details = el("details"); details.append(el("summary", "", "Day " + number(step.day) + " follow-up")); details.append(content); card.append(details); }
      else card.append(content);
    }); grid.append(card);
  }); host.append(grid);
}
function renderSignals(host) {
  const rows = filters(array(app.bundle.companies).flatMap(company => array(company.triggers).map(trigger => ({...trigger, company}))), row => row.company.segment_id);
  set("view-count", rows.length + " signals");
  host.append(el("p", "dialog-copy", "Hiring, funding, launches, and technology changes are context signals. They do not by themselves establish purchase intent."));
  if (!rows.length) return host.append(empty("No supported trigger events match this view. Missing dates remain unknown."));
  const grid = el("div", "cards-grid");
  rows.forEach(row => {
    const card = el("article", "research-card signal-card");
    append(card, append(el("div", "card-head"), labelled(row.company.name, words(row.kind)), tag(words(row.status), row.status)),
      el("span", "signal-date", formatDate(row.event_date)), el("p", "", row.summary), evidenceButton(row.company.name + " · " + words(row.kind), row));
    grid.append(card);
  }); host.append(grid);
}
function renderView() {
  if (!app.bundle) return;
  const host = $("view-content"); host.replaceChildren();
  $("view-tools").hidden = app.view === "overview";
  $("segment-filter").hidden = app.view === "competitors";
  document.querySelectorAll("[data-view]").forEach(button => {
    const active = button.dataset.view === app.view;
    button.classList.toggle("active", active); button.setAttribute("aria-pressed", text(active));
  });
  const renderers = {overview: renderOverview, companies: renderCompanies, people: renderPeople,
    competitors: renderCompetitors, campaigns: renderCampaigns, signals: renderSignals};
  renderers[app.view](host);
}
function renderBundle(bundle) {
  app.bundle = bundle; $("results").hidden = false; $("empty-state").hidden = true;
  $("intro").classList.add("compact"); $("demo-banner").hidden = bundle.mode !== "synthetic_demo";
  set("product-name", bundle.product?.name || "Research in progress");
  set("product-description", bundle.product?.description || bundle.product_url || "Your research pack will appear as each stage finishes.");
  const status = bundle.status || "running";
  set("run-status", words(status).toUpperCase());
  $("run-status").className = "pill " + (["blocked", "partial"].includes(status) ? "warning" : status === "failed" ? "danger" : "");
  const summary = bundle.summary || {}, targets = bundle.targets || {};
  const companies = array(bundle.companies), people = allPeople();
  const target = number(targets.companies || 100), ready = number(summary.outreach_ready_companies), enriched = number(summary.enriched_companies);
  set("enriched-count", enriched); set("company-target", target);
  $("enriched-meter").style.width = Math.min(100, target ? 100 * enriched / target : 0) + "%";
  set("company-count", companies.length); set("supported-count", number(summary.source_supported_companies) + " source-supported · " + ready + " outreach-ready");
  set("people-count", people.length); set("people-target", "/ " + (targets.people_min || 30) + "–" + (targets.people_max || 50));
  set("email-count", number(summary.verified_emails) + " verified emails");
  set("competitor-count", array(bundle.competitors).length); set("competitor-target", "/ " + (targets.competitors || 15));
  set("sequence-count", array(bundle.sequences).length); set("sequence-target", "/ " + number((targets.segments || 6) * (targets.sequences_per_segment || 3)));
  set("segment-count", array(bundle.segments).length + " / " + (targets.segments || 6) + " segments");
  set("tab-companies", companies.length); set("tab-people", people.length);
  const chosen = $("segment-filter").value;
  $("segment-filter").replaceChildren(el("option", "", "All segments")); $("segment-filter").firstChild.value = "";
  array(bundle.segments).forEach(segment => { const option = el("option", "", segment.name); option.value = segment.id; $("segment-filter").append(option); });
  if ([...$("segment-filter").options].some(option => option.value === chosen)) $("segment-filter").value = chosen;
  const warnings = array(bundle.warnings);
  $("warnings-panel").hidden = !warnings.length;
  set("warnings-heading", warnings.length + " research note" + (warnings.length === 1 ? "" : "s") + " · coverage and provider limitations");
  $("warnings-list").replaceChildren(...warnings.map(warning => el("li", "", warning)));
  set("run-stamp", "Updated " + formatDate(bundle.updated_at || bundle.created_at) + " · " + app.run);
  renderView();
}
async function loadRun(name, quiet = false) {
  if (!name) return;
  try { const bundle = await request("/api/runs/" + encodeURIComponent(name)); if (app.run === name) renderBundle(bundle); }
  catch (error) { if (!quiet) notice(error.message); }
}
function renderState(state) {
  $("provider-list").replaceChildren(...array(state.providers).map(provider => append(
    el("div", "provider-item" + (provider.configured ? "" : " missing")),
    el("strong", "", words(provider.category) + " · " + words(provider.provider)),
    el("span", provider.configured ? "" : "bad", provider.note))));
  $("usage-limits").replaceChildren(...Object.entries(state.limits || {}).map(([key, value]) => el("span", "limit-chip", words(key) + ": " + value)));
  if (!Object.keys(state.limits || {}).length) $("usage-limits").append(el("span", "muted", "Provider defaults apply; review the local settings file."));
  const picker = $("run-picker");
  const runs = [...array(state.runs)];
  if (state.job && !runs.some(run => run.name === state.job.run_name)) runs.unshift({name: state.job.run_name, product: "Research in progress", mode: "live"});
  const pickerStamp = JSON.stringify(runs);
  if (pickerStamp !== app.pickerStamp) {
    picker.replaceChildren(el("option", "", "Start a new research run")); picker.firstChild.value = "";
    runs.forEach(run => { const option = el("option", "", (run.mode === "synthetic_demo" ? "Demo · " : "") + run.product + " · " + run.name); option.value = run.name; picker.append(option); });
    app.pickerStamp = pickerStamp;
  }
  picker.value = app.run;
  app.busy = Boolean(state.job?.active);
  $("research-button").disabled = app.busy; $("demo-button").disabled = app.busy;
  $("research-button").textContent = app.busy ? "Research running…" : "Research website →";
  const selectedActive = app.busy && state.job?.run_name === app.run;
  $("progress-panel").hidden = !selectedActive;
  if (selectedActive) set("progress-title", words(state.job.stage || "Starting research"));
  if (state.job?.error && state.job.run_name === app.run) notice(state.job.error);
  if (!app.run && runs.length && !app.initialized) { app.run = runs[0].name; picker.value = app.run; loadRun(app.run, true); }
  app.initialized = true;
}
async function refresh() {
  if (app.refreshing) return;
  app.refreshing = true;
  try {
    const state = await request("/api/state"); renderState(state);
    const stamp = JSON.stringify(state.job);
    if (state.job?.run_name === app.run && (state.job.active || stamp !== app.jobStamp)) await loadRun(app.run, true);
    app.jobStamp = stamp;
  }
  catch (error) { notice(error.message || "The local server is unavailable. Keep the studio command running."); }
  finally { app.refreshing = false; }
}
async function begin(demo) {
  notice();
  try {
    $("research-button").disabled = true; $("demo-button").disabled = true;
    const job = await request(demo ? "/api/demo" : "/api/research", demo ? {} : {url: $("product-url").value.trim()});
    app.run = job.run_name; app.view = "overview"; $("table-search").value = "";
    renderBundle({product_url: demo ? "Fictional demo" : $("product-url").value, mode: demo ? "synthetic_demo" : "live", status: "running"});
    await refresh();
  } catch (error) { notice(error.message); $("research-button").disabled = app.busy; $("demo-button").disabled = app.busy; }
}
$("research-form").addEventListener("submit", event => { event.preventDefault(); begin(false); });
$("demo-button").addEventListener("click", () => begin(true));
$("run-picker").addEventListener("change", async event => {
  app.run = event.target.value; app.bundle = null; app.view = "overview"; notice();
  if (!app.run) { $("results").hidden = true; $("empty-state").hidden = false; $("intro").classList.remove("compact"); }
  else await loadRun(app.run);
});
document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => { app.view = button.dataset.view; $("table-search").value = ""; $("segment-filter").value = ""; renderView(); }));
$("table-search").addEventListener("input", renderView);
$("segment-filter").addEventListener("change", renderView);
$("close-dialog").addEventListener("click", () => $("evidence-dialog").close());
$("download-button").addEventListener("click", async () => {
  if (!app.run) return;
  const kinds = {overview: "companies", companies: "companies", people: "contacts", competitors: "competitors", campaigns: "sequences", signals: "triggers"};
  try {
    const kind = kinds[app.view], response = await fetch("/api/runs/" + encodeURIComponent(app.run) + "/export?kind=" + kind);
    if (!response.ok) { const result = await response.json(); throw new Error(result.error || "Export unavailable"); }
    const url = URL.createObjectURL(await response.blob()), a = el("a");
    a.href = url; a.download = app.run + "-" + kind + ".csv"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { notice(error.message); }
});
refresh();
app.refresh = setInterval(refresh, 2500);
