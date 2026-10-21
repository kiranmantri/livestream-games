"""Recalculate corpus scores of the phrases IN THE DB from the downloaded transcripts/chat."""
from __future__ import annotations

from pathlib import Path

import psycopg

from corpus.extract import count_targets, load_corpus, score_of


def compute(corpus: Path, texts: list[str]) -> tuple[dict[str, float], dict[str, dict], int]:
    """-> ({text: new score}, {text: raw counts}, number of transcripts)"""
    t_docs, c_docs, _, _, _ = load_corpus(corpus)
    n_tr = len(t_docs)
    counts = count_targets(t_docs, c_docs, texts)
    scores = {t: score_of(c["t_count"], c["t_streams"], c["c_authors"], n_tr, len(t.split()))
              for t, c in counts.items()}
    return scores, counts, n_tr


def rescore(conn: psycopg.Connection, corpus: Path, dry_run: bool = False) -> dict:
    """Replace score AND prior_score of every NON-manual phrase by the corpus-derived score (manual phrases keep
    their hand-set score; dealt/marked counters are kept). Phrases never found in the corpus get 0."""
    rows = conn.execute("SELECT id, text, score FROM phrases WHERE source <> 'manual' ORDER BY id").fetchall()
    scores, counts, n_tr = compute(corpus, [r[1] for r in rows])
    changed = absent = 0
    with conn.transaction():
        for pid, text, old in rows:
            new = scores[text]
            absent += not (counts[text]["t_count"] or counts[text]["c_msgs"])
            changed += abs(new - float(old)) > 1e-6
            if not dry_run:
                conn.execute("UPDATE phrases SET score = %s, prior_score = %s WHERE id = %s", (new, new, pid))
    vals = sorted(scores.values())
    return {"phrases": len(rows), "changed": changed, "not_found_in_corpus": absent, "transcripts": n_tr,
            "min": vals[0] if vals else None, "max": vals[-1] if vals else None, "dry_run": dry_run}
