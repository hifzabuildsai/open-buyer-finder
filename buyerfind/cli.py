"""Small public CLI; paid services and LLM calls are not required."""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .core import (atomic_text, collect, load_profile, load_run, now,
                   validate_reviews, write_run)
from .report import csv_text, metrics


def run_cli(args):
    if args.command in {"pipeline", "pack-demo", "studio", "monitor", "export", "sync-plan", "sync-apply", "activation-plan", "activation-apply"}:
        return run_market_cli(args)
    if args.command in {"search", "demo"}:
        profile = load_profile(args.profile)
        fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8")) if args.fixture else None
        if args.command == "demo" and fixture is None:
            raise ValueError("demo requires --fixture; synthetic results are explicitly labelled")
        if not 1 <= args.limit <= 50:
            raise ValueError("--limit must be between 1 and 50")
        # Validate before creating output. Existing runs are never overwritten by search.
        datetime.strptime(args.since, "%Y-%m-%d")
        output = Path(args.out)
        if output.exists():
            raise ValueError("Output already exists; choose a new directory to preserve the prior run")
        run = collect(profile, args.since, limit=args.limit, fixture=fixture)
        write_run(output, run)
        atomic_text(output / "candidates.csv", csv_text(run))
        print(json.dumps({"items": len(run["items"]), "mode": run["mode"],
                          "complete": run["complete"], "warnings": run["warnings"],
                          "report": str(output / "report.html")}, indent=2))
        return 0 if run["complete"] else 2
    run = load_run(args.run)
    if args.command == "review":
        reviews = json.loads(Path(args.file).read_text(encoding="utf-8"))
        validate_reviews(run, reviews)
        run["reviews"] = reviews
        run["reviewed_at"] = now()
        run["reviewer"] = args.reviewer
        write_run(args.run, run)
        atomic_text(Path(args.run) / "candidates.csv", csv_text(run))
        print(f"Applied {len(reviews)} cited reviews; factual fit still requires human review.")
    elif args.command == "feedback":
        if args.id not in {row["id"] for row in run["items"]}:
            raise ValueError("Unknown evidence id")
        run["feedback"] = [x for x in run.get("feedback", []) if x["id"] != args.id]
        run["feedback"].append({"id": args.id, "verdict": args.verdict,
                                "note": args.note, "labelled_at": now()})
        write_run(args.run, run)
        print(json.dumps(metrics(run), indent=2))
    elif args.command == "summary":
        print(json.dumps({"mode": run["mode"], "complete": run["complete"],
                          "items": len(run["items"]), "reviews": len(run["reviews"]),
                          **metrics(run)}, indent=2))
    return 0


def run_market_cli(args):
    from .market import demo_bundle, load_config, monitor_run, run_pipeline, save_bundle
    if args.command == "pipeline":
        bundle = run_pipeline(args.url, load_config(args.config), args.out)
        print(json.dumps({"status":bundle["status"],"summary":bundle["summary"],"warnings":bundle["warnings"],"report":str(Path(args.out)/"report.html")},indent=2))
        return 0 if bundle["status"]=="complete" else 2
    if args.command == "pack-demo":
        if Path(args.out).exists():
            raise ValueError("Output already exists")
        save_bundle(args.out,demo_bundle())
        print(f"Fictional demo saved: {Path(args.out)/'report.html'}")
        return 0
    if args.command == "studio":
        from .studio import serve
        serve(load_config(args.config),args.workspace,args.port)
        return 0
    if args.command == "monitor":
        bundle=monitor_run(args.run,load_config(args.config))
        print(json.dumps(bundle["monitor_history"][-1],indent=2))
        return 0
    from .exports import (export_csv, prepare_export, save_export_plan, apply_export,
                          prepare_activation, apply_activation)
    if args.command in {"export","sync-plan"}:
        bundle=json.loads((Path(args.run)/"bundle.json").read_text(encoding="utf-8"))
        if args.command=="export":
            Path(args.out).parent.mkdir(parents=True,exist_ok=True)
            atomic_text(args.out,export_csv(bundle,args.kind))
            print(f"Saved {args.out}")
        else:
            options=json.loads(Path(args.options).read_text(encoding="utf-8")) if args.options else {}
            plan=prepare_export(bundle,args.destination,options)
            save_export_plan(plan,args.out)
            print(json.dumps({"plan":args.out,"approval_hash":plan["approval_hash"],"note":"Review the plan before explicitly applying it."},indent=2))
    elif args.command=="sync-apply":
        print(json.dumps(apply_export(args.plan,args.approve),indent=2))
    elif args.command=="activation-plan":
        plan=prepare_activation(args.plan)
        save_export_plan(plan,args.out)
        print(json.dumps({"plan":args.out,"approval_hash":plan["approval_hash"],"note":"Applying this plan starts real email delivery."},indent=2))
    elif args.command=="activation-apply":
        print(json.dumps(apply_activation(args.plan,args.approve),indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Open Buyer Finder: public evidence before outreach.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    p=commands.add_parser("pipeline",help="Research a product URL using configured providers")
    p.add_argument("url"); p.add_argument("--config"); p.add_argument("--out",required=True)
    p=commands.add_parser("pack-demo",help="Create a fictional full-workflow demo")
    p.add_argument("--out",required=True)
    p=commands.add_parser("studio",help="Open the local founder dashboard")
    p.add_argument("--config"); p.add_argument("--workspace",default="runs"); p.add_argument("--port",type=int,default=8765)
    p=commands.add_parser("monitor",help="Refresh a live run's trigger events once")
    p.add_argument("run"); p.add_argument("--config")
    p=commands.add_parser("export",help="Download local CSV or CRM import template")
    p.add_argument("run"); p.add_argument("--kind",choices=("companies","contacts","competitors","sequences","triggers","hubspot","notion","instantly"),default="companies"); p.add_argument("--out",required=True)
    p=commands.add_parser("sync-plan",help="Prepare a CRM/provider write plan without applying it")
    p.add_argument("run"); p.add_argument("--destination",choices=("hubspot","notion","instantly"),required=True); p.add_argument("--options"); p.add_argument("--out",required=True)
    p=commands.add_parser("sync-apply",help="Apply an exact approved external-write plan")
    p.add_argument("plan"); p.add_argument("--approve",required=True)
    p=commands.add_parser("activation-plan",help="Read and prepare an Instantly campaign activation plan")
    p.add_argument("plan"); p.add_argument("--out",required=True)
    p=commands.add_parser("activation-apply",help="Start real email delivery for an approved campaign snapshot")
    p.add_argument("plan"); p.add_argument("--approve",required=True)
    for name in ("search", "demo"):
        p = commands.add_parser(name)
        p.add_argument("profile", help="Product/ICP/query JSON; see examples/fourgate.json")
        p.add_argument("--since", default=(datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat())
        p.add_argument("--limit", type=int, default=10)
        p.add_argument("--out", required=True)
        p.add_argument("--fixture", help="Offline API-response JSON; labels the report synthetic_demo")
    p = commands.add_parser("review")
    p.add_argument("run")
    p.add_argument("file", help="Cited review JSON array produced by a human or coding agent")
    p.add_argument("--reviewer", choices=("human", "coding-agent"), required=True)
    p = commands.add_parser("feedback")
    p.add_argument("run")
    p.add_argument("--id", required=True)
    p.add_argument("--verdict", choices=("relevant", "irrelevant", "unclear"), required=True)
    p.add_argument("--note", default="")
    p = commands.add_parser("summary")
    p.add_argument("run")
    args = parser.parse_args(argv)
    try:
        return run_cli(args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
