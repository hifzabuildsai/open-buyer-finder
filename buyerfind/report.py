"""Local, script-free HTML and spreadsheet-safe CSV reports."""
import csv
import html
import io


def e(value):
    return html.escape(str(value or ""), quote=True)


def decisions(run):
    return {row["id"]: row for row in run.get("reviews", [])}


def metrics(run):
    labels = run.get("feedback", [])
    decided = [x for x in labels if x["verdict"] in {"relevant", "irrelevant"}]
    relevant = sum(x["verdict"] == "relevant" for x in decided)
    return {"labelled": len(labels), "decided": len(decided), "relevant": relevant,
            "relevance_rate": relevant / len(decided) if decided else None}


def csv_text(run):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["id", "author", "source", "title", "url", "date", "decision", "reason"])
    reviewed = decisions(run)
    for row in run["items"]:
        review = reviewed.get(row["id"], {})
        values = [row["id"], row["author"], row["source"], row["title"], row["url"],
                  row["published_at"], review.get("decision", "unreviewed"), review.get("reason", "")]
        writer.writerow([("'" + str(v)) if str(v).lstrip().startswith(("=", "+", "-", "@"))
                         else str(v or "") for v in values])
    return stream.getvalue()


def render(run):
    reviewed = decisions(run)
    cards = []
    for index, row in enumerate(run["items"], 1):
        review = reviewed.get(row["id"], {})
        decision = review.get("decision", "unreviewed")
        notes = ""
        if review:
            notes = (f'<section class="review"><h3>Review · {e(decision)}</h3>'
                     f'<p>{e(review["reason"])}</p><blockquote>{e(review["evidence_quote"])}</blockquote>'
                     '<h4>Still unknown</h4><ul>'
                     + ''.join(f'<li>{e(x)}</li>' for x in review["unknowns"])
                     + f'</ul><p><b>Next question:</b> {e(review.get("next_question"))}</p></section>')
        cards.append(f'''<article><div class="eyebrow">{index:02d} · {e(row['source'])} · {e(decision)}</div>
<h2><a href="{e(row['url'])}" rel="noopener noreferrer">{e(row['title'])}</a></h2>
<p class="meta"><a href="{e(row['profile_url'])}">{e(row['author'])}</a> · Published {e(row['published_at'])}
 · State: {e(row['state'])}</p><p>{e(row['text'][:420])}</p>
<p class="meta">Keyword matches: {e(', '.join(row['matched_terms'])) or 'none'} · ID {e(row['id'])}</p>
{notes}<details><summary>Read retrieved evidence</summary><pre>{e(row['text'])}</pre></details></article>''')
    m = metrics(run)
    rate = "Not measured" if m["relevance_rate"] is None else f'{m["relevance_rate"]:.0%} ({m["relevant"]}/{m["decided"]})'
    warnings = ''.join(f'<li>{e(x)}</li>' for x in run["warnings"])
    notice = f'<aside><b>Incomplete research</b><ul>{warnings}</ul></aside>' if warnings else ''
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(run['profile']['name'])} · Open Buyer Finder</title>
<style>
:root{{font-family:system-ui,sans-serif;color:#17223b;background:#f2f4f8;line-height:1.6}}
body{{margin:0}}main{{max-width:980px;margin:auto;padding:44px 24px}}header{{padding:30px;background:#14213b;color:white;border-radius:20px}}
.eyebrow{{font-size:12px;letter-spacing:.12em;text-transform:uppercase;font-weight:700;color:#3d6cbb}}header .eyebrow{{color:#adceff}}
h1{{font-size:clamp(28px,5vw,46px);line-height:1.15;margin:16px 0}}h2{{font-size:21px;line-height:1.4;margin:10px 0}}
h3,h4{{margin-bottom:8px}}a{{color:#1b53a3;text-decoration:none}}a:hover{{text-decoration:underline}}
article{{background:white;border:1px solid #dce2ec;border-radius:16px;padding:26px;margin:20px 0}}
.meta,footer{{font-size:13px;color:#52627d}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:20px 0}}
.stat{{background:#fff;border-radius:12px;padding:18px;border:1px solid #dce2ec}}.stat b{{display:block;font-size:24px}}
aside{{background:#fff0d1;border-radius:12px;padding:20px}}blockquote{{margin:16px 0;padding-left:16px;border-left:3px solid #4c7ac6}}
.review{{background:#f4f7fc;border-radius:12px;padding:16px;margin-top:16px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace}}
summary{{cursor:pointer;color:#1b53a3}}footer{{margin:28px 0}}p,li{{overflow-wrap:anywhere}}
</style><main><header><div class="eyebrow">Open Buyer Finder · {e(run['mode'])}</div>
<h1>Evidence before outreach.</h1><p>{e(run['profile']['name'])}: {e(run['profile']['description'])}</p></header>
<div class="stats"><div class="stat"><b>{len(run['items'])}</b>Evidence items shown</div>
<div class="stat"><b>{len(reviewed)}</b>Items reviewed</div>
<div class="stat"><b>{e(rate)}</b>Founder-labelled relevance</div></div>{notice}
<p>Research since {e(run['since'])} · Retrieved {e(run['generated_at'])}
 · Reviewer: {e(run.get('reviewer', 'No review yet'))}</p>
<p><b>These are research candidates, not confirmed buyers.</b> Ordering uses keyword matches, not purchase probability.
AI review requires human judgment. Current need, authority, budget and compatibility remain unverified until established.</p>
{''.join(cards) or '<article><h2>No evidence items returned</h2><p>Check source status above, then revise the queries or date range.</p></article>'}
<footer>Local report · No scripts, external assets or tracking. Public source authors may be maintainers, users, competitors or researchers.
No emails have been generated or sent by this CLI. {e(m['labelled'])} founder labels recorded; unclear labels are excluded from the relevance denominator.</footer></main></html>'''
