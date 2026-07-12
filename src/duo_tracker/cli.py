import argparse
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="duo-tracker")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("migrate", help="Create / update Postgres schema (idempotent)")

    p_probe = sub.add_parser(
        "probe",
        help="Hit the (reverse-engineered) endpoints, dump raw JSON to data/, print shapes",
    )
    p_probe.add_argument("--person", default=None, help="Only probe this person")

    p_snap = sub.add_parser("snapshot", help="Fetch progress and upsert one row per person")
    p_snap.add_argument("--person", default=None, help="Only snapshot this person")
    p_snap.add_argument("--date", default=None, metavar="YYYY-MM-DD", help="Snapshot date (default: today)")

    p_back = sub.add_parser(
        "backfill",
        help="Fill past days' xp/lesson metrics from xp_summaries (position columns stay NULL)",
    )
    p_back.add_argument("--since", required=True, metavar="YYYY-MM-DD")
    p_back.add_argument("--person", default=None, help="Only backfill this person")

    p_show = sub.add_parser("show", help="Print recent snapshot rows")
    p_show.add_argument("--person", default=None, help="Only show this person")
    p_show.add_argument("--days", type=int, default=14, help="Look-back window (default: 14)")

    p_web = sub.add_parser("web", help="Serve the pace-log page")
    p_web.add_argument("--host", default="0.0.0.0")
    p_web.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)

    # Imports are deferred so e.g. `probe` works without a reachable database.
    if args.cmd == "migrate":
        from duo_tracker.core.migrate import run_migrations
        return run_migrations()
    if args.cmd == "probe":
        from duo_tracker.probe import run
        return run(person=args.person)
    if args.cmd == "snapshot":
        from duo_tracker.snapshot import run
        return run(person=args.person, date_str=args.date)
    if args.cmd == "backfill":
        from duo_tracker.backfill import run
        return run(since_str=args.since, person=args.person)
    if args.cmd == "show":
        from duo_tracker.show import run
        return run(person=args.person, days=args.days)
    if args.cmd == "web":
        from duo_tracker.web import serve
        return serve(host=args.host, port=args.port)
    return 2


if __name__ == "__main__":
    sys.exit(main())
