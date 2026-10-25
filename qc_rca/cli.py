"""Command-line entry points.

    python -m qc_rca.cli migrate                 # alembic upgrade head
    python -m qc_rca.cli ingest [--force] [--path P] [--synthetic]  # orders CSV (idempotent)
    python -m qc_rca.cli ingest-docs [--no-embed]
    python -m qc_rca.cli create-key NAME ROLE    # prints the key once
    python -m qc_rca.cli status
    python -m qc_rca.cli purge-history           # apply retention now
    python -m qc_rca.cli ask "Why did STORE_101 underperform?"
"""
import argparse
import json
import sys

from qc_rca.config import ROOT_DIR, get_settings


def cmd_migrate(_):
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT_DIR / "alembic.ini")), "head")
    print("migrations applied")


def cmd_ingest(args):
    from qc_rca.db.session import session_scope
    from qc_rca.ingestion import ingest_csv

    with session_scope() as s:
        res = ingest_csv(s, args.path or get_settings().data_csv_path, force=args.force, synthetic=args.synthetic)
    print(res.model_dump_json(indent=2))
    return 0 if res.status != "failed" else 1


def cmd_ingest_docs(args):
    from qc_rca.db.session import session_scope
    from qc_rca.rag.store import ingest_documents

    with session_scope() as s:
        print(json.dumps(ingest_documents(s, with_embeddings=not args.no_embed), indent=2))


def cmd_create_key(args):
    from qc_rca.db.models import Role
    from qc_rca.db.session import session_scope
    from qc_rca.security.auth import create_api_user

    with session_scope() as s:
        key = create_api_user(s, args.name, Role(args.role))
    print(f"API key for {args.name} ({args.role}) — store it now, it is not shown again:\n{key}")


def cmd_purge(_):
    from qc_rca.retention import purge_expired

    print(json.dumps(purge_expired(), indent=2))


def cmd_status(_):
    from qc_rca.analytics import service
    from qc_rca.llm.ollama import ollama_status
    from qc_rca.rag.store import corpus_stats

    out = {"ollama": ollama_status()}
    try:
        out["dates"] = service.available_dates()
        out["cities"] = len(service.list_cities())
        out["knowledge"] = corpus_stats()
    except Exception as e:  # noqa: BLE001
        out["database_error"] = f"{type(e).__name__}: {e}"[:300]
    print(json.dumps(out, indent=2, default=str))


def cmd_ask(args):
    from qc_rca.workflow.graph import run_investigation

    report = run_investigation(args.question)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return
    if report.get("narrative"):
        print(report["narrative"], "\n")
    print(report["answer_markdown"])
    print(f"\n[intent={report['intent']} via {report['intent_source']}, confidence={report['confidence']['level']}, "
          f"llm_used={report['llm']['used']}, {report['timings_ms'].get('total')} ms]")


def main(argv=None):
    p = argparse.ArgumentParser(prog="qc_rca")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    i = sub.add_parser("ingest")
    i.add_argument("--path")
    i.add_argument("--force", action="store_true")
    i.add_argument("--synthetic", action="store_true", help="flag rows as generated demo data")
    i.set_defaults(fn=cmd_ingest)
    d = sub.add_parser("ingest-docs")
    d.add_argument("--no-embed", action="store_true")
    d.set_defaults(fn=cmd_ingest_docs)
    k = sub.add_parser("create-key")
    k.add_argument("name")
    k.add_argument("role", choices=["viewer", "analyst", "admin"])
    k.set_defaults(fn=cmd_create_key)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("purge-history").set_defaults(fn=cmd_purge)
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--json", action="store_true")
    a.set_defaults(fn=cmd_ask)
    args = p.parse_args(argv)
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
