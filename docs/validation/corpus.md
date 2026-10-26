# Validation: Corpus

Corpus ingestion from YouTube (yt-dlp), phrase extraction, cliches, rescore.
Split out of AGENTS.md (see its "Validation docs" index). Blocks are in the order they were written;
later blocks supersede earlier ones where they disagree. Expected test counts are as of when each block was written.

### Validation for Task 2 (corpus fetch)
    uv run corpus fetch --last 5 --dry-run   # expect: 5 KEEP lines (1920x1080), SKIP(vertical) lines (1080x1920)
    uv run corpus fetch --last 5             # idempotent; skips files already on disk (long on first run)
    ls -l data/corpus/*/                     # expect per video: <id>.pt-orig.vtt (+ <id>.live_chat.json)
    python3 -c "import json;[print(x['video_id'],x['orientation'],x['errors']) for x in json.load(open('data/corpus/manifest.json'))]"
    # expect: all horizontal; a stream without a chat track shows 'chat: no live_chat track available'
    # reset: rm -rf data/corpus/*/ data/corpus/manifest.json

### Validation for Task 3 (phrase extraction)
    uv run corpus suggest --show 60          # expect: "5 transcripts, 4 chats (10583 chat msgs)" + ranked table
                                             # columns: /hour = uses per stream-hour; t_str = streams (of 5) containing it
    uv run corpus suggest --write 400        # merges candidates into corpus/phrases.csv (keeps manual rows + your edits)
    uv run corpus add "minha frase aqui"     # manual phrase (3-5 words, else error exit 1)
    # CURATE: open corpus/phrases.csv, set enabled to 0 for rows you don't want (or delete them), add rows via `corpus add`.
    # re-running `suggest --write` never overwrites your enabled edits or manual rows ("+0 new").
    awk -F, 'NR>1{c[$3]++}END{for(k in c)print k,c[k]}' corpus/phrases.csv   # count by source
    # reset: rm corpus/phrases.csv

### Validation for the corpus overhaul (20 streams, cliches, rescore, bulk delete) - YOU run these (long / touch dev DB)
    uv run migrate                                    # expect: applied 0002 ... 0005 (then "up to date")
    nohup uv run corpus fetch --last 20 --scan 80 > /tmp/fetch20.log 2>&1 &     # hours; idempotent, re-run to resume
    tail -f /tmp/fetch20.log                          # expect KEEP / SKIP(vertical) lines, then a summary
    uv run corpus suggest --show 80                   # expect: "20 transcripts", cliche authors matched (names + channel ids),
                                                      # kinds {'repeated','cliche','author'}; skips phrases already in seed/DB
    uv run corpus suggest --write 400                 # appends NEW rows to corpus/phrases.csv, all enabled=0
    uv run corpus export                              # FIRST (optional): make the seed file equal the DB (DB is the truth)
    uv run corpus load --new                          # inserts CSV rows missing from the DB as INACTIVE; never changes existing
    uv run corpus rescore --dry-run                   # expect: counts (changed / not_found_in_corpus / min / max), writes nothing
    uv run corpus rescore                             # replaces score AND prior_score of every non-manual phrase
    docker compose exec db psql -U livegames -c "select source, enabled, count(*), round(avg(score),2) from phrases group by 1,2 order by 1,2"
    # UI: /admin/phrases -> tick rows, "Selecionar todas" / "Limpar seleção" / "Excluir selecionadas" (asks to confirm;
    #   refused if fewer than 24 enabled would remain). Phrases of 1-10 words can be added manually.
    uv run pytest -q                                  # expect: 333 passed
    # Cliche sources: corpus/cliches.txt (3-10 word expressions, matched ignoring case/accents, >= 1 occurrence) and
    #   corpus/cliche_authors.txt (aliases >= 5 letters; chat display name CONTAINING an alias, '@' and punctuation ignored).
