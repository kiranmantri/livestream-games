"""Corpus CLI: fetch (download streams) / suggest (find candidate phrases) / rescore / load (seed an empty DB)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SEED_SQL = Path("deploy/seed/phrases_seed.sql")   # made from the cloud DB by deploy/make_seed.py


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
                   help="INSERT the NEW candidates into the database (DATABASE_URL) as INACTIVE: all cliche/author ones "
                        "+ top N repeated; phrases already in the DB are skipped, existing rows never change")

    seed_help = ("seed the phrases table from the SQL seed file; only runs when the table is EMPTY "
                 "(the DB is the source of truth)")
    ld = sub.add_parser("load", help=seed_help, description=seed_help)
    ld.add_argument("--sql", type=Path, default=SEED_SQL, metavar="FILE", help=f"seed file (default {SEED_SQL})")

    rs = sub.add_parser("rescore", help="replace the score of every NON-manual phrase in the DB with the one "
                                        "recalculated from data/corpus (manual phrases keep theirs)")
    rs.add_argument("--corpus", type=Path, default=Path("data/corpus"))
    rs.add_argument("--dry-run", action="store_true", help="compute and report, write nothing")

    ri = sub.add_parser("replay-import", help="store the cleaned transcript + chat of a downloaded video in the DB (replay_events)")
    ri.add_argument("video_id")
    ri.add_argument("--corpus", type=Path, default=Path("data/corpus"))
    ri.add_argument("--force", action="store_true", help="replace the rows of this video (do NOT do it during a game)")

    rsd = sub.add_parser("replay-seed", help="write deploy/seed/replay_<video_id>.sql from the replay rows in the DB (for the cloud)")
    rsd.add_argument("video_id")
    rsd.add_argument("--out", type=Path, default=None)

    rl = sub.add_parser("replay-load", help="apply the video's SQL seed, only if the DB has no rows of that video")
    rl.add_argument("video_id", nargs="?", default=None, help="default: FALACIAS_VIDEO_ID")

    sub.add_parser("replay-emoji", help="rewrite YouTube emoji shortcodes (:face-blue-smiling:) in replay_events to Unicode; idempotent, never touches running games")

    sub.add_parser("fallacies-load", help="apply deploy/seed/fallacies_seed.sql, only if the fallacies table is EMPTY")
    sub.add_parser("fallacies-manual", help="add the requested manual fallacies (Deus das lacunas, Ônus da prova invertido) if missing by name")
    sub.add_parser("fallacies-top", help="rename/enable the curated Wikipedia fallacies and disable the other Wikipedia rows (OVERWRITES online edits of those rows; manual, never at boot)")

    args = p.parse_args(argv)
    if args.cmd == "fallacies-top":
        from app.db import connect
        from app.falacias import sync_top_fallacies
        conn = connect()
        try:
            print("fallacies:", sync_top_fallacies(conn))
        finally:
            conn.close()
        return 0
    if args.cmd == "fallacies-manual":
        from app.db import connect
        from app.fallacies_admin import ensure_manual
        conn = connect()
        try:
            print("fallacies:", {"added": ensure_manual(conn)})
        finally:
            conn.close()
        return 0
    if args.cmd == "fallacies-load":
        from app.db import connect
        from app.falacias import load_fallacy_seed
        conn = connect()
        try:
            print("fallacies:", load_fallacy_seed(conn))
        finally:
            conn.close()
        return 0
    if args.cmd == "replay-emoji":
        from app.db import connect
        from corpus.emoji import fix_replay_events
        conn = connect()
        try:
            print("replay rows rewritten:", fix_replay_events(conn))
        finally:
            conn.close()
        return 0
    if args.cmd in ("replay-import", "replay-seed", "replay-load"):
        from app.db import connect
        from corpus import replay
        conn = connect()
        try:
            if args.cmd == "replay-import":
                print("replay rows:", replay.import_video(conn, args.video_id, args.corpus, force=args.force))
            elif args.cmd == "replay-seed":
                from app.feed import seed_path
                out = args.out or seed_path(args.video_id)
                print(f"{replay.write_seed(conn, args.video_id, out)} rows -> {out} ({out.stat().st_size // 1024} KiB)")
            else:
                from app.feed import load_replay_seed
                print("replay:", load_replay_seed(conn, args.video_id))
        except (replay.ReplayImportError, ValueError) as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        finally:
            conn.close()
        return 0
    if args.cmd == "load":
        from app.db import connect
        from corpus.load import load_sql_seed
        conn = connect()
        try:
            print("phrases:", load_sql_seed(conn, args.sql))
        finally:
            conn.close()
        return 0
    if args.cmd == "rescore":
        import psycopg

        from app.config import database_url
        from corpus.rescore import rescore
        with psycopg.connect(database_url()) as conn:
            print(rescore(conn, args.corpus, dry_run=args.dry_run))
        return 0
    if args.cmd == "fetch":
        from corpus.fetch import fetch
        return fetch(args.last, args.out, args.scan, args.dry_run)
    if args.cmd == "suggest":
        import psycopg

        from app.config import database_url
        from corpus.extract import build, insert_candidates, load_cliches
        known: set[str] = set()
        try:
            with psycopg.connect(database_url(), connect_timeout=3) as conn:
                known = {r[0] for r in conn.execute("SELECT text FROM phrases")}
            print(f"excluding phrases already in the DB: {len(known)}")
        except (psycopg.Error, RuntimeError) as e:     # reading is fine without a DB; writing is not
            if args.write:
                print(f"ERROR: --write needs the database ({type(e).__name__})", file=sys.stderr)
                return 1
            print(f"WARNING: DB not read ({type(e).__name__}); nothing is excluded", file=sys.stderr)
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
            from app.db import connect
            conn = connect()
            try:
                added, total = insert_candidates(conn, rows, args.write)
            finally:
                conn.close()
            print(f"\nDB: +{added} new phrases (INACTIVE), {total} phrases in total")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
