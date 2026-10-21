"""Corpus CLI: fetch / suggest / add."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PHRASES = Path("corpus/phrases.csv")  # tracked: the curated phrase list (loaded to DB in task 4)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="corpus")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download transcripts + chat of the last N horizontal streams")
    f.add_argument("--last", type=int, default=20)
    f.add_argument("--out", type=Path, default=Path("data/corpus"))
    f.add_argument("--scan", type=int, default=80, help="how many recent videos to inspect")
    f.add_argument("--dry-run", action="store_true", help="only list KEEP/SKIP decisions")

    s = sub.add_parser("suggest", help="find 3-10 word candidates (repeated + cliche/fallacy list + cliche chat authors)")
    s.add_argument("--corpus", type=Path, default=Path("data/corpus"))
    s.add_argument("--show", type=int, default=60, help="rows to print")
    s.add_argument("--write", type=int, default=0, metavar="N",
                   help="append NEW candidates to corpus/phrases.csv as INACTIVE: all cliche/author ones + top N repeated; "
                        "phrases already in the seed file or the DB are skipped, existing rows never change")

    a = sub.add_parser("add", help="add a manual phrase to the CSV SEED (a populated DB ignores it: use /admin/phrases)")
    a.add_argument("text")

    seed_help = ("seed corpus/phrases.csv (+ video manifest) into PostgreSQL; the phrases table is only "
                 "seeded when EMPTY (the DB is the source of truth)")
    ld = sub.add_parser("load", help=seed_help, description=seed_help)
    ld.add_argument("--force", action="store_true",
                    help="re-import every CSV row: overwrites score/enabled of matching phrases and "
                         "re-adds deleted ones")
    ld.add_argument("--new", action="store_true",
                    help="insert only CSV rows missing from the DB, as INACTIVE; never changes existing rows")

    ld.add_argument("--sql", type=Path, metavar="FILE",
                    help="seed from a SQL file made by deploy/make_seed.py instead of the CSV (still only when EMPTY)")

    rs = sub.add_parser("rescore", help="replace the score of every NON-manual phrase in the DB with the one "
                                        "recalculated from data/corpus (manual phrases keep theirs)")
    rs.add_argument("--corpus", type=Path, default=Path("data/corpus"))
    rs.add_argument("--dry-run", action="store_true", help="compute and report, write nothing")
    sub.add_parser("export", help="write the DB phrases to corpus/phrases.csv (DB = source of truth)")

    args = p.parse_args(argv)
    if args.cmd == "load":
        import psycopg

        from app.config import database_url
        from corpus.load import load_phrases, load_sql_seed, load_videos
        if args.sql:
            from app.db import connect
            conn = connect()
            try:
                print("phrases:", load_sql_seed(conn, args.sql))
            finally:
                conn.close()
            return 0
        with psycopg.connect(database_url()) as conn:
            print("phrases:", load_phrases(conn, PHRASES, force=args.force, new_only=args.new))
            manifest = Path("data/corpus/manifest.json")
            if manifest.exists():
                print("videos upserted:", load_videos(conn, manifest))
        return 0
    if args.cmd == "rescore":
        import psycopg

        from app.config import database_url
        from corpus.rescore import rescore
        with psycopg.connect(database_url()) as conn:
            print(rescore(conn, args.corpus, dry_run=args.dry_run))
        return 0
    if args.cmd == "export":
        import psycopg

        from app.config import database_url
        from corpus.rescore import export_seed
        with psycopg.connect(database_url()) as conn:
            print(f"{PHRASES}: {export_seed(conn, PHRASES)} phrases exported from the DB")
        return 0
    if args.cmd == "fetch":
        from corpus.fetch import fetch
        return fetch(args.last, args.out, args.scan, args.dry_run)
    if args.cmd == "suggest":
        from corpus.extract import build, load_cliches, read_seed, write_phrases
        known = {r["text"] for r in read_seed(PHRASES)}
        try:
            import psycopg

            from app.config import database_url
            with psycopg.connect(database_url(), connect_timeout=3) as conn:
                known |= {r[0] for r in conn.execute("SELECT text FROM phrases")}
            print(f"excluding phrases already known: {len(known)} (seed file + DB)")
        except (psycopg.Error, RuntimeError) as e:  # DB optional here: the seed file alone is used
            print(f"WARNING: DB not read ({type(e).__name__}); excluding only the seed file ({len(known)})",
                  file=sys.stderr)
        rows, meta = build(args.corpus, exclude=known)
        kinds = {k: sum(r["kind"] == k for r in rows) for k in ("repeated", "cliche", "author")}
        print(f"streams: {meta['transcripts']} transcripts, {meta['chats']} chats ({meta['chat_msgs']} chat msgs)")
        print(f"new candidates: {len(rows)} {kinds}; skipped as already known: {meta['already_known']}")
        usable, skipped = load_cliches()
        print(f"cliche list: {len(usable)} usable, {len(skipped)} skipped (not 3-10 words)")
        print("cliche authors matched in chat:",
              {f"{n} ({a[:8]})": c for (n, a), c in sorted(meta["author_matches"].items())} or "NONE")
        print(f"{'phrase':44}{'kind':10}{'src':16}{'t_cnt':>6}{'t_str':>6}{'c_aut':>6}{'score':>7}")
        for r in rows[:args.show]:
            print(f"{r['text'][:43]:44}{r['kind']:10}{r['source']:16}{r['t_count']:>6}"
                  f"{r['t_streams']:>6}{r['c_authors']:>6}{r['score']:>7}")
        if args.write:
            added, total = write_phrases(rows, PHRASES, args.write)
            print(f"\n{PHRASES}: +{added} new (INACTIVE), {total} total")
        return 0
    if args.cmd == "add":
        from corpus.extract import add_manual
        try:
            print(add_manual(args.text, PHRASES))
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
