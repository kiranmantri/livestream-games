"""Corpus tooling: 3-10 word extraction, cliche list/authors, seed-file rules, DB rescore, `load --new`."""
from __future__ import annotations

import csv
import json

import pytest

from app.db import connect
from corpus import extract as X
from corpus.load import load_phrases as seed
from corpus.rescore import export_seed, rescore
from tests.test_cards import load_phrases

CLICHE = "isso é uma falácia clássica de debate"     # 6 words


def vtt(sentences: list[str]) -> str:
    lines = ["WEBVTT", ""]
    for i, s in enumerate(sentences):
        lines += [f"00:00:{i:02d}.000 --> 00:00:{i + 1:02d}.000", "ignored previous line",
                  " ".join(f"{w}<00:00:{i:02d}.500><c> </c>" for w in s.split()), ""]
    return "\n".join(lines)


def chat_line(author_id: str, name: str, text: str) -> str:
    r = {"authorName": {"simpleText": name}, "authorExternalChannelId": author_id,
         "message": {"runs": [{"text": text}]}}
    return json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {
        "liveChatTextMessageRenderer": r}}}]}})


@pytest.fixture
def corpus(tmp_path):
    """3 streams, 1 hour each. REPEATED phrase in every transcript; CLICHE once in one transcript; authors in chat."""
    rep = "vida depois da morte existe realmente"
    man = []
    for i in range(3):
        sents = []
        for j in range(6):                       # distinct filler so n-grams can't run across sentence borders
            sents += [rep, f"bom dia a todos vocês numero{j}", f"ruido{j} qualquer sobre assunto{j} diferente"]
        if i == 0:
            sents.append(CLICHE)
        (tmp_path / f"v{i}.pt-orig.vtt").write_text(vtt(sents), encoding="utf-8")
        msgs = [chat_line("UCx", "Pessoa Comum", "ninguém merece esse argumento")]
        msgs += [chat_line("UCo", "Olidia Santos", "isso prova que deus existe")] if i == 0 else []
        msgs += [chat_line("UCr", "@RafaOliveira", "sinceramente não entendi nada mesmo")] if i == 1 else []
        msgs += [chat_line("UCz", "Raf", "texto de quem nao e autor aqui")]
        (tmp_path / f"v{i}.live_chat.json").write_text("\n".join(msgs), encoding="utf-8")
        man.append({"video_id": f"v{i}", "transcript": f"v{i}.pt-orig.vtt", "chat": f"v{i}.live_chat.json",
                    "duration": 3600})
    (tmp_path / "manifest.json").write_text(json.dumps(man))
    return tmp_path


# ------------------------------------------------------------------ pure rules
@pytest.mark.parametrize("text,ok", [
    ("hoje em dia", True), ("um dois", False), ("a vida é bela", False),         # starts with a function word
    ("vida depois da morte", True), ("uma frase de dez palavras " + "x y z w", False),
    (" ".join(["palavra"] * 10), True), (" ".join(["palavra"] * 11), False),
    ("bom dia pessoal aqui", False), ("ele disse tudo bem sim", False),
])
def test_valid_rules(text, ok):
    assert X.valid(text) is ok


def test_sentence_ok_allows_stopword_edges_but_not_greetings_or_bad_length():
    assert X.sentence_ok("isso prova que deus existe") and not X.valid("isso prova que deus existe")
    assert not X.sentence_ok("isso prova") and not X.sentence_ok("bom dia a todos vocês") \
        and not X.sentence_ok(" ".join(["w"] * 11))


def test_count_levels_finds_ten_word_grams_and_respects_threshold():
    ten = ["um", "dois", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez"]
    docs = [("a", [ten]), ("b", [ten]), ("c", [["quatro", "cinco", "seis"]])]
    total, who = X.count_levels(docs, min_count=2)
    assert total[" ".join(ten)] == 2 and who[" ".join(ten)] == {"a", "b"}
    assert total["quatro cinco seis"] == 3
    assert "um dois" not in total and not any(len(k.split()) > 10 for k in total)
    assert X.count_levels(docs, min_count=3)[0].keys() == {"quatro cinco seis"}


def test_count_levels_once_counts_one_vote_per_document():
    docs = [("a", [["x", "y", "z"], ["x", "y", "z"]])]
    assert X.count_levels(docs, 1)[0]["x y z"] == 2
    assert X.count_levels(docs, 1, once=True)[0]["x y z"] == 1


def test_count_targets_ignores_case_and_accents_and_needs_no_threshold():
    docs = [("s1", [["isso", "é", "uma", "falacia", "classica", "de", "debate"]])]
    got = X.count_targets(docs, [("u1", X.segments("ISSO é uma falácia clássica de debate!"))], [CLICHE])
    assert got[CLICHE] == {"t_count": 1, "t_streams": 1, "c_msgs": 1, "c_authors": 1}


def test_score_grows_with_spread_and_length_and_is_capped():
    assert X.score_of(5, 1, 0, 5, 3) < X.score_of(5, 5, 0, 5, 3) < X.score_of(5, 5, 3, 5, 3)
    assert X.score_of(5, 5, 3, 5, 3) < X.score_of(5, 5, 3, 5, 8)
    assert X.score_of(10**9, 5, 10**6, 5, 10) == X.MAX_AUTO_SCORE
    assert X.score_of(0, 0, 0, 5, 3) == 0


def test_cliche_list_files_are_valid():
    ok, bad = X.load_cliches()
    assert len(ok) >= 30 and bad == [], bad
    assert all(3 <= len(t.split()) <= 10 for t in ok)
    aliases = [X.alias_key(a) for a in X.read_list(X.AUTHORS_FILE)]
    assert {"olidia", "jaidielson", "aquiles", "corvo", "batman", "rafaelaoliveira", "rafaoliveira"} <= set(aliases)
    assert all(len(a) >= 5 for a in aliases)                                     # short aliases match strangers


# ------------------------------------------------------------------ build
def test_build_finds_repeated_cliche_and_author_phrases_but_never_greetings(corpus):
    rows, meta = X.build(corpus, cliches=[CLICHE, "frase que nunca foi dita aqui"],
                         author_aliases=["olidia", "rafaoliveira"])
    by = {r["text"]: r for r in rows}
    # captions have no punctuation, so the longest repeated n-gram may spill over the sentence border (pruning keeps it)
    rep_rows = [r for r in rows if "vida depois da morte existe realmente" in r["text"]]
    assert len(rep_rows) == 1 and rep_rows[0]["kind"] == "repeated" and rep_rows[0]["t_streams"] == 3
    assert by[CLICHE]["kind"] == "cliche" and by[CLICHE]["t_count"] == 1          # found once is enough
    assert "frase que nunca foi dita aqui" not in by                              # listed but absent: not a candidate
    assert by["isso prova que deus existe"]["kind"] == "author"
    assert by["sinceramente não entendi nada mesmo"]["kind"] == "author"          # '@RafaOliveira' matched by alias
    assert "texto de quem nao e autor aqui" not in by                             # 'Raf' is not an alias
    assert not any(X.has_greeting(t) for t in by)
    assert all(3 <= r["words"] <= 10 for r in rows)
    assert {n for n, _ in meta["author_matches"]} == {"Olidia Santos", "@RafaOliveira"}
    # 'ninguém merece esse argumento' is repeated in chat by ONE author only -> not a candidate
    assert "ninguém merece esse argumento" not in by


def test_build_excludes_known_phrases_ignoring_accents_and_case(corpus):
    rows, meta = X.build(corpus, cliches=[CLICHE], author_aliases=[], exclude={"Isso É uma FALACIA clássica de debate"})
    assert CLICHE not in {r["text"] for r in rows} and meta["already_known"] >= 1


def test_write_phrases_appends_inactive_and_never_touches_existing_rows(corpus, tmp_path):
    seed_csv = tmp_path / "seed.csv"
    X.write_seed([{"text": "minha frase manual", "source": "manual", "score": "99", "enabled": "1"},
                  {"text": "vida depois da morte existe realmente", "source": "chat", "score": "7", "enabled": "1"}],
                 seed_csv)
    rows, _ = X.build(corpus, cliches=[CLICHE], author_aliases=["olidia"], exclude=None)
    added, total = X.write_phrases(rows, seed_csv, top=50)
    got = {r["text"]: r for r in X.read_seed(seed_csv)}
    assert added == total - 2 and added >= 2
    assert got["minha frase manual"]["enabled"] == "1" and got["minha frase manual"]["score"] == "99"
    assert got["vida depois da morte existe realmente"]["score"] == "7"            # existing row untouched
    assert got[CLICHE]["enabled"] == "0" and got["isso prova que deus existe"]["enabled"] == "0"
    assert all(r["enabled"] == "0" for t, r in got.items() if r["source"] != "manual" and t != "vida depois da morte existe realmente")
    assert X.write_phrases(rows, seed_csv, top=50)[0] == 0                       # idempotent


def test_add_manual_allows_1_to_10_words(tmp_path):
    f = tmp_path / "s.csv"
    assert "added" in X.add_manual("Palavra", f) and "already" in X.add_manual("palavra", f)
    X.add_manual(" ".join(["w"] * 10), f)
    for bad in ("", " ".join(["w"] * 11), "bom dia"):
        with pytest.raises(ValueError):
            X.add_manual(bad, f)
    assert {r["source"] for r in X.read_seed(f)} == {"manual"}


# ------------------------------------------------------------------ DB: rescore / load --new / export
@pytest.fixture
def db(test_db_url):
    c = connect(test_db_url)
    yield c
    c.execute("TRUNCATE game_sessions, users, phrases, bingo_cards, bingo_marks CASCADE")
    c.close()


def test_rescore_replaces_scores_of_non_manual_phrases_only(db, corpus):
    load_phrases(db, n=3)
    db.execute("DELETE FROM phrases")
    db.execute("INSERT INTO phrases (text, source, score, dealt, marked) VALUES "
               "('vida depois da morte existe realmente','chat',3,10,4),"
               "('frase que nunca aparece no corpus','chat',8,0,0),"
               "('minha frase manual','manual',99,0,0)")
    out = rescore(db, corpus, dry_run=True)
    assert out["dry_run"] and db.execute("SELECT score FROM phrases WHERE source='chat' ORDER BY id").fetchall() == [(3,), (8,)]
    out = rescore(db, corpus)
    assert out == {**out, "phrases": 2, "changed": 2, "not_found_in_corpus": 1, "transcripts": 3}
    rows = {t: (float(s), float(p), d, m) for t, s, p, d, m in
            db.execute("SELECT text, score, prior_score, dealt, marked FROM phrases")}
    new = rows["vida depois da morte existe realmente"]
    assert new[0] > 3 and new[0] == new[1] and new[2:] == (10, 4)                # score = prior, counters kept
    assert rows["frase que nunca aparece no corpus"][:2] == (0.0, 0.0)
    assert rows["minha frase manual"][0] == 99.0                                 # manual untouched


def test_load_new_adds_only_missing_rows_inactive_and_never_overwrites(db, tmp_path):
    f = tmp_path / "p.csv"
    X.write_seed([{"text": "frase que ja existe", "source": "chat", "score": "9", "enabled": "1"},
                  {"text": "frase nova do corpus", "source": "transcript", "score": "5", "enabled": "1"},
                  {"text": "dois", "source": "chat", "score": "1", "enabled": "1"},             # 1 word: ok (admin-shortened)
                  {"text": " ".join(["w"] * 11), "source": "chat", "score": "1", "enabled": "1"}], f)   # 11 words: bad
    db.execute("INSERT INTO phrases (text, source, score) VALUES ('frase que ja existe', 'chat', 2)")
    out = seed(db, f, new_only=True)
    assert out["added"] == 2 and out["rejected"] == 1
    rows = {t: (float(s), e) for t, s, e in db.execute("SELECT text, score, enabled FROM phrases")}
    assert rows == {"frase que ja existe": (2.0, True), "frase nova do corpus": (5.0, False), "dois": (1.0, False)}
    assert seed(db, f, new_only=True)["added"] == 0
    assert "skipped" in seed(db, f)                                              # plain load still seed-only


def test_export_writes_the_db_back_to_the_seed_file(db, tmp_path):
    db.execute("INSERT INTO phrases (text, source, score, enabled) VALUES "
               "('uma frase de teste','chat',4.5,false),('manual um','manual',99,true)")
    f = tmp_path / "out.csv"
    assert export_seed(db, f) == 2
    with f.open(newline="", encoding="utf-8") as h:
        got = list(csv.DictReader(h))
    assert got == [{"text": "manual um", "source": "manual", "score": "99", "enabled": "1"},
                   {"text": "uma frase de teste", "source": "chat", "score": "4.5", "enabled": "0"}]


def test_sql_seed_loads_only_into_an_empty_table_and_is_not_reapplied(db, tmp_path):
    from corpus.load import load_sql_seed
    f = tmp_path / "seed.sql"
    f.write_text("BEGIN;\nINSERT INTO phrases (text, source, score, prior_score, enabled) VALUES\n"
                 "('frase um do seed','chat',4,4,true),('frase dois do seed','manual',99,99,false)\n"
                 "ON CONFLICT (text) DO NOTHING;\nCOMMIT;\n", encoding="utf-8")
    assert load_sql_seed(db, f) == {"phrases": 2, "enabled": 1, "videos": 0}
    db.execute("DELETE FROM phrases WHERE text = 'frase um do seed'")             # admin deletes one in the UI
    assert "skipped" in load_sql_seed(db, f)                                       # restart: must NOT bring it back
    assert db.execute("SELECT count(*) FROM phrases").fetchone() == (1,)


def test_sql_seed_refuses_a_non_autocommit_connection(db, tmp_path, test_db_url):
    import psycopg

    from corpus.load import load_sql_seed
    f = tmp_path / "seed.sql"
    f.write_text("SELECT 1;", encoding="utf-8")
    with psycopg.connect(test_db_url) as plain, pytest.raises(RuntimeError):
        load_sql_seed(plain, f)
