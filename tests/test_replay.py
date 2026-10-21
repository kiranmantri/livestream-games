"""Task 2a: reading a recorded video's transcript + chat into replay rows (corpus/replay.py)."""
from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

from app.db import connect
from corpus import replay as R

VTT = """WEBVTT
Kind: captions
Language: pt-orig

00:00:01.000 --> 00:00:03.000 align:start position:0%

primeira linha<00:00:01.500><c> do</c><00:00:02.000><c> texto</c>

00:00:03.000 --> 00:00:03.010 align:start position:0%
primeira linha do texto

00:00:03.010 --> 00:00:05.000 align:start position:0%
primeira linha do texto
[Música] segunda<00:00:03.500><c> linha</c><00:00:04.000><c>   com   espaços</c>

00:01:02.250 --> 00:01:04.000 align:start position:0%
segunda linha com espaços
&gt;&gt; Ele disse<00:01:03.000><c> que</c><00:01:03.500><c> R&amp;D</c><00:01:03.900><c> importa</c>

01:00:00.000 --> 01:00:02.000 align:start position:0%
x
[risadas]<00:00:00.000><c></c>

01:00:02.000 --> 01:00:04.000 align:start position:0%
x
última<00:01:03.000><c> linha</c>
"""


def text_run(t):
    return {"text": t}


def chat_line(offset, renderer, body):
    return json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(offset),
                                                "actions": [{"addChatItemAction": {"item": {renderer: body}}}]}})


def msg(runs, author="@ana "):
    return {"authorName": {"simpleText": author}, "message": {"runs": runs}}


@pytest.fixture
def video(tmp_path):
    d = tmp_path / "vid1"
    d.mkdir()
    (d / "vid1.pt-orig.vtt").write_text(VTT, encoding="utf-8")
    lines = [
        chat_line(5000, "liveChatTextMessageRenderer", msg([text_run("oi "), text_run("pessoal")])),
        chat_line(1000, "liveChatTextMessageRenderer", msg([text_run("antes"), {"emoji": {"emojiId": "🌹"}}], "@bia")),
        chat_line(2000, "liveChatTextMessageRenderer", msg([{"emoji": {"emojiId": "UCabc/xyz", "isCustomEmoji": True,
                                                                       "shortcuts": [":_logo:"]}}, text_run(" ok")])),
        chat_line(2500, "liveChatTextMessageRenderer", msg([{"emoji": {"emojiId": "UCabc/xyz", "isCustomEmoji": True}}])),
        chat_line(3000, "liveChatTextMessageRenderer", msg([text_run("   ")])),
        chat_line(-50, "liveChatTextMessageRenderer", msg([text_run("negativo")])),
        chat_line(6000, "liveChatPaidMessageRenderer", {**msg([text_run("super obrigado")]), "purchaseAmountText": {"simpleText": "R$2.00"}}),
        chat_line(6500, "liveChatPaidMessageRenderer", {"authorName": {"simpleText": "@mudo"}}),
        chat_line(7000, "liveChatViewerEngagementMessageRenderer", msg([text_run("Live chat replay is on")])),
        json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": "8000", "actions": [
            {"addChatItemAction": {"item": {"giftMessageViewModel": {"text": {"content": "sent Garlic"}}}}}]}}),
        "",
        "not json at all",
    ]
    (d / "vid1.live_chat.json").write_text("\n".join(lines), encoding="utf-8")
    return d


# ---------------------------------------------------------------- transcript
def test_transcript_keeps_only_timed_lines_with_the_cue_start_time(video):
    got = R.read_transcript(video / "vid1.pt-orig.vtt")
    assert [(ms, t) for ms, t in got][:2] == [(1000, "primeira linha do texto"), (3010, "segunda linha com espaços")]


def test_transcript_strips_annotations_entities_and_empty_lines(video):
    got = dict(R.read_transcript(video / "vid1.pt-orig.vtt"))
    assert got[62250] == ">> Ele disse que R&D importa"
    assert 3_600_000 not in got                                   # "[risadas]" alone is dropped
    assert got[3_602_000] == "última linha" and len(got) == 4


def test_transcript_offsets_are_non_decreasing(video):
    ms = [m for m, _ in R.read_transcript(video / "vid1.pt-orig.vtt")]
    assert ms == sorted(ms)


# ---------------------------------------------------------------- chat
def test_chat_text_paid_and_emoji_rules(video):
    got = R.read_chat(video / "vid1.live_chat.json")
    texts = [(ms, a, t) for ms, a, t in got]
    assert (5000, "@ana", "oi pessoal") in texts                   # runs joined, author trimmed
    assert (1000, "@bia", "antes🌹") in texts                       # unicode emoji kept
    assert (2000, "@ana", ":_logo: ok") in texts                   # custom emoji -> its shortcut
    assert (6000, "@ana", "super obrigado") in texts               # paid message TEXT is shown
    assert (0, "@ana", "negativo") in texts                        # negative offsets clamp to 0


def test_chat_skips_blank_gifts_system_and_paid_without_text(video):
    got = R.read_chat(video / "vid1.live_chat.json")
    assert len(got) == 5
    assert {t for _, _, t in got} == {"oi pessoal", "antes🌹", ":_logo: ok", "negativo", "super obrigado"}


def test_chat_is_sorted_by_offset_and_stable(video):
    ms = [m for m, _, _ in R.read_chat(video / "vid1.live_chat.json")]
    assert ms == sorted(ms)


def test_real_corpus_numbers_if_downloaded():
    d = Path("data/corpus/_nVWPIFCUzQ")
    if not d.exists():
        pytest.skip("test video not downloaded")
    t = R.read_transcript(next(d.glob("*.vtt")))
    c = R.read_chat(next(d.glob("*.live_chat.json")))
    assert 13_000 < len(t) < 14_000 and t[0][0] < 120_000 and t[-1][0] > 30_000_000
    assert 2_700 < len(c) < 2_760
    assert all("[" not in x for _, x in t) and all(x == x.strip() and x for _, _, x in c)


# ---------------------------------------------------------------- import into the DB
@pytest.fixture
def conn(test_db_url):
    c = connect(test_db_url)
    c.execute("TRUNCATE replay_events")
    yield c
    c.execute("TRUNCATE replay_events")
    c.close()


def counts(conn, vid="vid1"):
    return dict(conn.execute("SELECT kind, count(*) FROM replay_events WHERE video_id = %s GROUP BY 1", (vid,)).fetchall())


def test_import_stores_both_tracks(conn, video):
    res = R.import_video(conn, "vid1", video.parent)
    assert res == {"transcript": 4, "chat": 5}
    assert counts(conn) == {"transcript": 4, "chat": 5}
    assert conn.execute("SELECT author FROM replay_events WHERE kind = 'transcript' LIMIT 1").fetchone()[0] is None


def test_import_twice_is_a_skip_and_force_replaces(conn, video):
    R.import_video(conn, "vid1", video.parent)
    conn.execute("UPDATE replay_events SET text = 'editado' WHERE kind = 'chat' AND offset_ms = 5000")
    assert "skipped" in R.import_video(conn, "vid1", video.parent)
    assert conn.execute("SELECT count(*) FROM replay_events WHERE text = 'editado'").fetchone()[0] == 1
    assert R.import_video(conn, "vid1", video.parent, force=True) == {"transcript": 4, "chat": 5}
    assert conn.execute("SELECT count(*) FROM replay_events WHERE text = 'editado'").fetchone()[0] == 0


def test_import_does_not_touch_other_videos(conn, video):
    conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, text) VALUES ('other','chat',1,'x')")
    R.import_video(conn, "vid1", video.parent, force=True)
    assert counts(conn, "other") == {"chat": 1}


def test_import_with_a_missing_or_empty_track_fails_and_stores_nothing(conn, video):
    (video / "vid1.live_chat.json").unlink()
    with pytest.raises(R.ReplayImportError):
        R.import_video(conn, "vid1", video.parent)
    assert counts(conn) == {}
    (video / "vid1.live_chat.json").write_text("", encoding="utf-8")
    with pytest.raises(R.ReplayImportError):
        R.import_video(conn, "vid1", video.parent)
    assert counts(conn) == {}
    with pytest.raises(R.ReplayImportError):
        R.import_video(conn, "nao-existe", video.parent)


def test_video_id_is_validated(conn, video):
    for bad in ("", "a b", "../x", "x;y", "a" * 40):
        with pytest.raises(ValueError):
            R.import_video(conn, bad, video.parent)


def test_import_is_durable_from_another_connection(conn, video, test_db_url):
    R.import_video(conn, "vid1", video.parent)
    with psycopg.connect(test_db_url) as other:
        assert other.execute("SELECT count(*) FROM replay_events").fetchone()[0] == 9


# ---------------------------------------------------------------- SQL seed
def test_seed_round_trip_is_identical_and_idempotent(conn, video, tmp_path):
    R.import_video(conn, "vid1", video.parent)
    before = conn.execute("SELECT kind, offset_ms, author, text FROM replay_events ORDER BY kind, offset_ms, text").fetchall()
    out = tmp_path / "replay_vid1.sql"
    assert R.write_seed(conn, "vid1", out) == 9
    conn.execute("TRUNCATE replay_events")
    conn.execute(out.read_text(encoding="utf-8"))
    conn.execute(out.read_text(encoding="utf-8"))                              # twice: no duplicates
    after = conn.execute("SELECT kind, offset_ms, author, text FROM replay_events ORDER BY kind, offset_ms, text").fetchall()
    assert after == before


def test_seed_escapes_quotes_and_percent(conn, tmp_path):
    conn.execute("INSERT INTO replay_events (video_id, kind, offset_ms, author, text) VALUES "
                 "('v','chat',1,'O''Neil','d''ele: 100% \\ \"ok\" ; DROP TABLE x;--')")
    out = tmp_path / "s.sql"
    R.write_seed(conn, "v", out)
    conn.execute("TRUNCATE replay_events")
    conn.execute(out.read_text(encoding="utf-8"))
    assert conn.execute("SELECT author, text FROM replay_events").fetchone() == ("O'Neil", "d'ele: 100% \\ \"ok\" ; DROP TABLE x;--")


def test_seed_of_an_unknown_video_is_refused(conn, tmp_path):
    with pytest.raises(R.ReplayImportError):
        R.write_seed(conn, "nada", tmp_path / "s.sql")
    assert not (tmp_path / "s.sql").exists()


def test_replay_unique_index_rejects_exact_duplicates(conn):
    q = "INSERT INTO replay_events (video_id, kind, offset_ms, text) VALUES ('v','chat',1,'a')"
    conn.execute(q)
    with pytest.raises(errors.UniqueViolation):
        conn.execute(q)


# ---------------------------------------------------------------- CLI
def test_cli_import_seed_load_round_trip(conn, video, tmp_path, test_db_url, monkeypatch, capsys):
    from corpus import cli
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    assert cli.main(["replay-import", "vid1", "--corpus", str(video.parent)]) == 0
    assert "'transcript': 4" in capsys.readouterr().out
    out = tmp_path / "replay_vid1.sql"
    assert cli.main(["replay-seed", "vid1", "--out", str(out)]) == 0
    assert "9 rows" in capsys.readouterr().out and out.exists()
    conn.execute("TRUNCATE replay_events")
    monkeypatch.setattr("app.feed.seed_path", lambda v: out)
    assert cli.main(["replay-load", "vid1"]) == 0
    assert "'inserted': 9" in capsys.readouterr().out
    assert cli.main(["replay-load", "vid1"]) == 0
    assert "skipped" in capsys.readouterr().out


def test_cli_force_replaces_the_rows(conn, video, test_db_url, monkeypatch, capsys):
    from corpus import cli
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    assert cli.main(["replay-import", "vid1", "--corpus", str(video.parent)]) == 0
    conn.execute("UPDATE replay_events SET text = 'editado' WHERE kind = 'chat' AND offset_ms = 5000")
    assert cli.main(["replay-import", "vid1", "--corpus", str(video.parent)]) == 0
    assert "skipped" in capsys.readouterr().out.splitlines()[-1]
    assert cli.main(["replay-import", "vid1", "--corpus", str(video.parent), "--force"]) == 0
    assert conn.execute("SELECT count(*) FROM replay_events WHERE text = 'editado'").fetchone()[0] == 0


def test_cli_reports_errors_without_a_traceback(conn, tmp_path, test_db_url, monkeypatch, capsys):
    from corpus import cli
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    assert cli.main(["replay-import", "nao-existe", "--corpus", str(tmp_path)]) == 1
    assert cli.main(["replay-import", "../x", "--corpus", str(tmp_path)]) == 1
    assert cli.main(["replay-seed", "nada"]) == 1
    assert capsys.readouterr().err.count("ERROR:") == 3


def test_cli_fallacies_load_seeds_an_empty_table_once(conn, test_db_url, monkeypatch, capsys):
    from corpus import cli
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    conn.execute("DELETE FROM fallacies")
    assert cli.main(["fallacies-load"]) == 0
    assert "'inserted': 97" in capsys.readouterr().out
    assert conn.execute("SELECT count(*) FILTER (WHERE enabled), count(*) FROM fallacies").fetchone() == (20, 97)
    assert cli.main(["fallacies-load"]) == 0
    assert "skipped" in capsys.readouterr().out
    conn.execute("DELETE FROM fallacies")


def test_cli_fallacies_top_syncs_the_enabled_flags(conn, test_db_url, monkeypatch, capsys):
    from corpus import cli
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    conn.execute("DELETE FROM fallacies")
    assert cli.main(["fallacies-load"]) == 0
    conn.execute("UPDATE fallacies SET enabled = true")
    capsys.readouterr()
    assert cli.main(["fallacies-top"]) == 0
    out = capsys.readouterr().out
    assert "'enabled': 0" in out and "'disabled': 77" in out and "'missing': []" in out      # the seed already had only 20 on, then all 97 were switched on
    assert conn.execute("SELECT count(*) FILTER (WHERE enabled) FROM fallacies").fetchone()[0] == 20
    conn.execute("DELETE FROM fallacies")
