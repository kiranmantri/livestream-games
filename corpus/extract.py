"""Phrase extraction from transcripts + live chat.

Candidates (all 3-10 words, never greetings) come from THREE sources:
  repeated : n-grams said often (transcript: >= min_t_count times in >= min_streams streams;
             chat: >= min_authors distinct authors)
  cliche   : expressions listed in corpus/cliches.txt (debate cliches / fallacies), found >= once
  author   : every 3-10 word sentence written in chat by the authors in corpus/cliche_authors.txt

`write_phrases` appends only phrases that are NOT already in the seed file / DB, and always INACTIVE.
"""
from __future__ import annotations

import csv
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

MIN_WORDS = 3            # extracted phrases
MAX_WORDS = 10
MANUAL_MIN_WORDS = 1     # manual phrases (admin)
MANUAL_MAX_WORDS = 10
MAX_AUTO_SCORE = 20.0    # cap of the corpus score (99 is reserved for manual phrases)

HERE = Path(__file__).parent
CLICHES_FILE = HERE / "cliches.txt"
AUTHORS_FILE = HERE / "cliche_authors.txt"

TAG_RE = re.compile(r"<[^>]+>")
TIMED_RE = re.compile(r"<\d\d:\d\d:\d\d\.\d+>")
WORD_RE = re.compile(r"[\wÀ-ÿ]+(?:[-'][\wÀ-ÿ]+)*", re.UNICODE)
SPLIT_RE = re.compile(r"[.!?,;:()\[\]\"“”…—–]+|\s-\s")
MENTION_RE = re.compile(r"@\S+")
URL_RE = re.compile(r"https?://\S+")
BRACKET_RE = re.compile(r"\[[^\]]*\]")  # caption sound annotations: [Música], [limpando a garganta]

# Function words: an n-gram may not START or END with one (prevents "de que a").
STOP = set("""
a o as os um uma uns umas de do da dos das em no na nos nas por pra para pro com sem sob sobre
e ou mas que se como quando onde porque pois já também só mais menos muito muita muitos muitas
eu tu ele ela nós nos vocês você voce eles elas me te lhe meu minha meus minhas seu sua seus suas
teu tua isso isto aquilo esse essa esses essas este esta estes estas aquele aquela aqui ali lá
é são era foi ser está estão tá tão estar tem têm ter tinha há vai vão vou foi ir fazer faz
não sim então aí né tipo assim ainda mesmo cada outro outra outros outras qual quais quem
ao aos à às num numa dum duma até depois antes entre ne ta to pq vc vcs tb tbm
""".split())  # noqa: SIM905

# Greetings / courtesy formulas are excluded (matched anywhere inside the phrase).
GREETINGS = [
    "bom dia", "boa tarde", "boa noite", "tudo bem", "tudo certo", "tudo joia", "bem-vindo",
    "bem-vinda", "bem vindo", "bem vinda", "olá", "obrigado", "obrigada", "boa semana",
    "fica à vontade", "fique à vontade", "até amanhã", "até logo", "feliz natal", "feliz ano",
    "como vai", "como vão", "como você está", "como está", "noite a todos", "tarde a todos",
    "dia a todos", "bem-vindos", "bem-vindas", "fiquem à vontade", "ficam à vontade",
]
_GREET_RE = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(g) for g in GREETINGS) + r")(?!\w)")


# ------------------------------------------------------------------ reading
def vtt_text(path: Path) -> list[str]:
    """YouTube rolling captions: only the line carrying inline <time> tags is new text."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if TIMED_RE.search(line):
            t = TAG_RE.sub("", line).strip()
            if t:
                out.append(t)
    return out


def chat_messages(path: Path) -> list[tuple[str, str, str]]:
    """-> [(author_channel_id, author_display_name, text)]"""
    msgs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        for a in d.get("replayChatItemAction", {}).get("actions", []):
            r = a.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
            if not r:
                continue
            text = "".join(run.get("text", "") for run in r["message"].get("runs", []))
            msgs.append((r.get("authorExternalChannelId", "?"),
                         r.get("authorName", {}).get("simpleText", ""), text))
    return msgs


def segments(text: str) -> list[list[str]]:
    """Split on punctuation, then tokenize to lowercase words."""
    text = URL_RE.sub(" ", MENTION_RE.sub(" ", BRACKET_RE.sub(" ", text))).lower()
    text = re.sub(r"k{3,}", "kkk", text)
    segs = []
    for part in SPLIT_RE.split(text):
        words = [w for w in WORD_RE.findall(part) if not w.isdigit()]
        if words:
            segs.append(words)
    return segs


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def has_greeting(text: str) -> bool:
    return bool(_GREET_RE.search(text))


def valid(phrase: str) -> bool:
    """Rules for EXTRACTED phrases."""
    if _GREET_RE.search(phrase):
        return False
    w = phrase.split()
    if not MIN_WORDS <= len(w) <= MAX_WORDS:
        return False
    return w[0] not in STOP and w[-1] not in STOP


def sentence_ok(phrase: str) -> bool:
    """Rules for WHOLE sentences (cliche authors): word count + no greeting; stop-word edges are fine here."""
    return MIN_WORDS <= len(phrase.split()) <= MAX_WORDS and not _GREET_RE.search(phrase)


def alias_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", strip_accents(name.lower()))


# ------------------------------------------------------------------ counting
Doc = tuple[str, list[list[str]]]      # (who, segments): who = stream id (transcript) or author id (chat)


def count_levels(docs: list[Doc], min_count: int, once: bool = False):
    """Level-wise (apriori) n-gram counting for n = MIN_WORDS..MAX_WORDS: an n-gram is only counted when both
    of its (n-1)-word halves were frequent, so 10-grams over millions of words stay cheap.
    once=True counts a gram at most once per document (chat: one vote per message).
    -> (total: {gram: count}, who: {gram: set of `who`}) for grams with count >= min_count."""
    total: dict[str, int] = {}
    who: dict[str, set] = {}
    prev: set[tuple] | None = None
    for n in range(MIN_WORDS, MAX_WORDS + 1):
        cnt: Counter = Counter()
        for _, segs in docs:
            seen: set = set()
            for words in segs:
                for i in range(len(words) - n + 1):
                    g = tuple(words[i:i + n])
                    if prev is not None and (g[:-1] not in prev or g[1:] not in prev):
                        continue
                    if once:
                        if g in seen:
                            continue
                        seen.add(g)
                    cnt[g] += 1
        freq = {g for g, c in cnt.items() if c >= min_count}
        if not freq:
            break
        owners: dict[tuple, set] = {g: set() for g in freq}      # second pass: owners only for frequent grams
        for w, segs in docs:
            for words in segs:
                for i in range(len(words) - n + 1):
                    g = tuple(words[i:i + n])
                    if g in owners:
                        owners[g].add(w)
        for g in freq:
            key = " ".join(g)
            total[key], who[key] = cnt[g], owners[g]
        prev = freq
    return total, who


def count_targets(t_docs: list[Doc], c_docs: list[Doc], texts: list[str]) -> dict[str, dict]:
    """Occurrences of SPECIFIC phrases (accent- and case-insensitive), with no frequency threshold.
    -> {text: {t_count, t_streams, c_msgs, c_authors}}"""
    def toks(t: str) -> tuple:
        return tuple(strip_accents(w) for seg in segments(t) for w in seg)

    targets: dict[tuple, list[str]] = defaultdict(list)
    for t in texts:
        targets[toks(t)].append(t)
    lengths = sorted({len(k) for k in targets if k})
    stats = {t: {"t_count": 0, "t_streams": set(), "c_msgs": 0, "c_authors": set()} for t in texts}

    def scan(docs, once, on_hit):
        for w, segs in docs:
            seen: set = set()
            for words in segs:
                sw = [strip_accents(x) for x in words]
                for n in lengths:
                    for i in range(len(sw) - n + 1):
                        g = tuple(sw[i:i + n])
                        if g in targets and not (once and g in seen):
                            seen.add(g)
                            on_hit(w, g)

    def t_hit(w, g):
        for t in targets[g]:
            stats[t]["t_count"] += 1
            stats[t]["t_streams"].add(w)

    def c_hit(w, g):
        for t in targets[g]:
            stats[t]["c_msgs"] += 1
            stats[t]["c_authors"].add(w)

    scan(t_docs, False, t_hit)
    scan(c_docs, True, c_hit)
    return {t: {"t_count": s["t_count"], "t_streams": len(s["t_streams"]), "c_msgs": s["c_msgs"],
                "c_authors": len(s["c_authors"])} for t, s in stats.items()}


def score_of(t_count: int, t_streams: int, c_authors: int, n_streams: int, nw: int) -> float:
    """Corpus score: how widely a phrase is said in transcripts (log count x share of streams) plus how many
    distinct chat authors wrote it, both boosted for longer expressions. Capped at MAX_AUTO_SCORE."""
    bonus = 1 + 0.35 * (nw - 1)
    t_part = math.log1p(t_count) * (t_streams / max(n_streams, 1)) * bonus if t_count else 0.0
    c_part = math.log1p(c_authors) * bonus
    return round(min(t_part + c_part, MAX_AUTO_SCORE), 3)


def prune_subsumed(rows: list[dict], ratio: float = 0.8) -> list[dict]:
    """Drop a phrase if a longer phrase containing it carries >= ratio of its weight."""
    by_text = {r["text"]: r for r in rows}
    drop = set()
    for r in rows:
        w = r["text"].split()
        if len(w) < 2:
            continue
        for n in range(1, len(w)):  # every sub-phrase of r
            for i in range(len(w) - n + 1):
                sub = " ".join(w[i:i + n])
                s = by_text.get(sub)
                if s and r["weight"] >= ratio * s["weight"]:
                    drop.add(sub)
    return [r for r in rows if r["text"] not in drop]


# ------------------------------------------------------------------ lists
def read_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]


def load_cliches(path: Path = CLICHES_FILE) -> tuple[list[str], list[str]]:
    """-> (usable cliches, skipped because not 3-10 words / greeting)"""
    ok, bad = [], []
    for ln in read_list(path):
        t = " ".join(ln.lower().split())
        (ok if MIN_WORDS <= len(t.split()) <= MAX_WORDS and not has_greeting(t) else bad).append(t)
    return list(dict.fromkeys(ok)), bad


def load_corpus(corpus: Path):
    """-> (transcript docs, chat docs (one per message), raw chat [(author_id, name, text)], total hours)"""
    manifest = json.loads((corpus / "manifest.json").read_text())
    tr_docs: list[Doc] = []
    for m in manifest:
        if m["transcript"]:
            tr_docs.append((m["video_id"], segments(" ".join(vtt_text(corpus / m["transcript"])))))
    chat_raw: list[tuple[str, str, str]] = []
    for m in manifest:
        if m["chat"]:
            chat_raw += chat_messages(corpus / m["chat"])
    c_docs: list[Doc] = [(a, segments(t)) for a, _, t in chat_raw]
    hours = sum((m.get("duration") or 0) for m in manifest if m["transcript"]) / 3600 or 1.0
    n_chats = len({m["video_id"] for m in manifest if m["chat"]})
    return tr_docs, c_docs, chat_raw, hours, n_chats


# ------------------------------------------------------------------ build
def build(corpus: Path, min_streams: int = 3, min_t_count: int = 5, min_authors: int = 2,
          min_per_hour: float = 0.1, max_per_hour: float = 8.0,
          cliches: list[str] | None = None, author_aliases: list[str] | None = None,
          exclude: set[str] | None = None):
    """-> (rows, meta). rows: dicts with text, words, source, kind, counts, score, weight.
    `exclude` = phrases already known (seed file / DB); compared accent- and case-insensitively."""
    tr_docs, c_docs, chat_raw, hours, n_chats = load_corpus(corpus)
    n_tr = len(tr_docs)
    min_streams = min(min_streams, n_tr)
    cliches = load_cliches()[0] if cliches is None else cliches
    aliases = [alias_key(a) for a in (read_list(AUTHORS_FILE) if author_aliases is None else author_aliases)]
    known = {strip_accents(" ".join(t.lower().split())) for t in (exclude or ())}

    t_total, t_who = count_levels(tr_docs, min_t_count)
    c_total, c_who = count_levels(c_docs, min_authors, once=True)

    def row(text, source, kind, t_count, t_streams, c_auth, c_msgs):
        nw = len(text.split())
        return {"text": text, "words": nw, "source": source, "kind": kind, "t_count": t_count,
                "per_hour": round(t_count / hours, 2), "t_streams": t_streams, "c_authors": c_auth,
                "c_msgs": c_msgs, "score": score_of(t_count, t_streams, c_auth, n_tr, nw),
                "weight": max(t_count, c_auth)}

    # 1) repeated
    rep = []
    for g in set(t_total) | set(c_total):
        if not valid(g):
            continue
        tc, ts, ca = t_total.get(g, 0), len(t_who.get(g, ())), len(c_who.get(g, ()))
        per_hour = tc / hours
        in_t = ts >= min_streams and tc >= min_t_count and min_per_hour <= per_hour <= max_per_hour
        in_c = ca >= min_authors and per_hour <= max_per_hour
        if not (in_t or in_c):
            continue
        rep.append(row(g, "transcript+chat" if in_t and in_c else ("transcript" if in_t else "chat"),
                       "repeated", tc if in_t else 0, ts if in_t else 0, ca if in_c else 0,
                       c_total.get(g, 0) if in_c else 0))
    rep = prune_subsumed(rep)

    # 2) cliches from the curated list (found at least once, however rare)
    found = count_targets(tr_docs, c_docs, cliches)
    cli = []
    for t, s in found.items():
        if s["t_count"] or s["c_msgs"]:
            src = "transcript+chat" if s["t_count"] and s["c_msgs"] else ("transcript" if s["t_count"] else "chat")
            cli.append(row(t, src, "cliche", s["t_count"], s["t_streams"], s["c_authors"], s["c_msgs"]))

    # 3) sentences written by the cliche authors
    matched: Counter = Counter()
    sent: dict[str, dict] = {}
    for author, name, text in chat_raw:
        key = alias_key(name)
        if not any(a and a in key for a in aliases):
            continue
        matched[(name, author)] += 1
        for words in segments(text):
            g = " ".join(words)
            if sentence_ok(g):
                s = sent.setdefault(g, {"msgs": 0, "authors": set()})
                s["msgs"] += 1
                s["authors"].add(author)
    aut = [row(g, "chat", "author", 0, 0, len(s["authors"]), s["msgs"]) for g, s in sent.items()]

    merged: dict[str, dict] = {}
    for r in rep + cli + aut:                       # first kind wins: repeated > cliche > author
        merged.setdefault(r["text"], r)
    rows = [r for r in merged.values() if strip_accents(r["text"]) not in known]
    rows.sort(key=lambda r: (-r["score"], r["text"]))
    meta = {"transcripts": n_tr, "chats": n_chats, "chat_msgs": len(chat_raw),
            "cliche_list": len(cliches), "author_matches": dict(matched),
            "already_known": len(merged) - len(rows)}
    return rows, meta


# ------------------------------------------------------------------ seed file
FIELDS = ["text", "source", "score", "enabled"]      # same columns as the DB export


def select(rows: list[dict], top: int) -> list[dict]:
    """Balanced pick of REPEATED phrases so chat-only/transcript-only gems aren't crowded out by 'both'."""
    quota = {"transcript+chat": top // 2, "chat": top // 4, "transcript": top - top // 2 - top // 4}
    picked: list[dict] = []
    for src, q in quota.items():
        picked += [r for r in rows if r["source"] == src][:q]
    if len(picked) < top:  # a source ran short: fill with the best remaining
        ids = {r["text"] for r in picked}
        picked += [r for r in rows if r["text"] not in ids][:top - len(picked)]
    return sorted(picked, key=lambda r: -r["score"])


def read_seed(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_seed(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (-float(r.get("score") or 0), r["text"])):
            w.writerow({k: r.get(k, "") for k in FIELDS})


def write_phrases(rows: list[dict], path: Path, top: int) -> tuple[int, int]:
    """Append NEW candidates to the seed file, always INACTIVE (enabled=0). Existing rows are never touched.
    Takes ALL cliche + author candidates and the best `top` repeated ones. -> (added, total rows)."""
    existing = read_seed(path)
    known = {strip_accents(r["text"]) for r in existing}
    chosen = [r for r in rows if r["kind"] != "repeated"] + select([r for r in rows if r["kind"] == "repeated"], top)
    added = 0
    for r in chosen:
        k = strip_accents(r["text"])
        if k in known:
            continue
        known.add(k)
        existing.append({"text": r["text"], "source": r["source"], "score": r["score"], "enabled": "0"})
        added += 1
    write_seed(existing, path)
    return added, len(existing)


def add_manual(text: str, path: Path) -> str:
    """Add a MANUAL phrase (1-10 words) to the seed file, active, score 99."""
    text = " ".join(text.lower().split())
    n = len(text.split())
    if not MANUAL_MIN_WORDS <= n <= MANUAL_MAX_WORDS:
        raise ValueError(f"manual phrase must have {MANUAL_MIN_WORDS}-{MANUAL_MAX_WORDS} words (got {n}): {text!r}")
    if _GREET_RE.search(text):
        raise ValueError(f"greetings are not allowed: {text!r}")
    existing = read_seed(path)
    if any(r["text"] == text for r in existing):
        return f"already present: {text!r}"
    existing.append({"text": text, "source": "manual", "score": "99", "enabled": "1"})
    write_seed(existing, path)
    return f"added manual: {text!r}"
