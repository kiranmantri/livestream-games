"""Build the fallacy seed (SQL) from the Portuguese Wikipedia "Lista de falácias", pinned to one revision.

    uv run python -m deploy.make_fallacies_seed                       # fetch the pinned revision, write the SQL seed
    uv run python -m deploy.make_fallacies_seed --from-file tests/fixtures/lista_de_falacias_70610117.json   # offline

The app never calls Wikipedia; this script is run by us. Output: deploy/seed/fallacies_seed.sql, loaded only into an
EMPTY `fallacies` table (table created by migration 0008). Every INSERT is `ON CONFLICT DO NOTHING`.
Only the curated top 20 (app.falacias.TOP_FALLACIES) are enabled; the other rows are imported disabled.
Text © Wikipedia contributors, CC BY-SA 4.0 (show the attribution wherever the list appears).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from app.falacias import FALLACY_NAMES, TOP_FALLACIES

OLDID = 70610117
API = "https://pt.wikipedia.org/w/api.php"
WIKI = "https://pt.wikipedia.org"
USER_AGENT = "live-games-seed/1.0"
OUT = Path("deploy/seed/fallacies_seed.sql")
FORMAL_H2 = "Falácias formais"
INFORMAL_H2 = "Falácias informais"
SEP = " – "                       # name – description (en dash with spaces)
ALT_SEP = " - "                   # a few entries use a plain hyphen
MAX_NAME = 120

# Well-known fallacies in debates like this channel's: label -> accent-folded substrings that count as present.
# Reported (not failed) when absent from the page, so we can add them as source='manual'.
MUST_HAVE = {
    "deus das lacunas": ("deus das lacunas", "god of the gaps"),
    "onus da prova invertido": ("onus da prova", "inversao do onus"),
    "espantalho": ("espantalho",),
    "ad hominem": ("ad hominem",),
    "falso dilema / falsa dicotomia": ("falso dilema", "falsa dicotomia"),
    "apelo a autoridade": ("apelo a autoridade", "verecundiam"),
    "ladeira escorregadia": ("ladeira escorregadia", "slippery slope", "bola de neve"),
    "petição de principio": ("petitio principii", "peticao de principio"),
    "post hoc": ("post hoc",),
    "apelo a emocao": ("apelo a emocao",),
    "apelo a ignorancia": ("apelo a ignorancia", "ad ignorantiam"),
    "tu quoque": ("tu quoque",),
    "falacia genetica": ("falacia genetica",),
    "escoces de verdade": ("escoces de verdade", "no true scotsman"),
    "apelo a natureza": ("apelo a natureza",),
    "pergunta complexa": ("pergunta complexa",),
}


@dataclass(frozen=True)
class Fallacy:
    name: str
    description: str
    category: str           # nearest heading (h4 if present, else h3)
    kind: str               # 'formal' | 'informal'
    url: str | None
    parent: str | None      # name of the item this one is nested under, if any

    @property
    def enabled(self) -> bool:
        """Only the curated top 20 (app.falacias.TOP_FALLACIES) are switched on; everything else is seeded disabled."""
        return self.name in TOP_FALLACIES


def fold(s: str) -> str:
    """Lowercase, no accents: used to compare names."""
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[Fallacy] = []
        self.h2 = self.h3 = self.h4 = ""
        self._head: str | None = None        # tag of the heading being read
        self._head_text = ""
        self._stack: list[dict] = []         # open <li>: {"text": [], "url": None, "name": None}
        self._skip = 0                       # depth inside <sup> (citations)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("h2", "h3", "h4"):
            self._head, self._head_text = tag, ""
        elif tag == "li":
            self._stack.append({"text": [], "url": None, "name": None})
        elif tag == "sup":
            self._skip += 1
        elif tag == "a" and self._stack and self._skip == 0:
            top = self._stack[-1]
            href = a.get("href") or ""
            if top["url"] is None and not top["text"] and href.startswith("/wiki/") \
                    and "new" not in (a.get("class") or ""):
                top["url"] = WIKI + href.split("#")[0]
        elif tag == "ul" and self._stack:
            self._close_text()               # text before a nested list belongs to the parent item

    def handle_endtag(self, tag):
        if tag in ("h2", "h3", "h4") and self._head == tag:
            text = clean(self._head_text)
            if tag == "h2":
                self.h2, self.h3, self.h4 = text, "", ""
            elif tag == "h3":
                self.h3, self.h4 = text, ""
            else:
                self.h4 = text
            self._head = None
        elif tag == "sup":
            self._skip = max(0, self._skip - 1)
        elif tag == "li" and self._stack:
            self._close_text()
            self._stack.pop()

    def handle_data(self, data):
        if self._head:
            self._head_text += data
        elif self._stack and self._skip == 0 and self._stack[-1]["name"] is None:
            self._stack[-1]["text"].append(data)

    def _close_text(self) -> None:
        """Emit the item whose own text has just ended (end of li, or start of a nested list)."""
        top = self._stack[-1]
        if top["name"] is not None:
            return
        top["name"] = ""                      # mark as closed even if skipped
        kind = {FORMAL_H2: "formal", INFORMAL_H2: "informal"}.get(self.h2)
        text = clean("".join(top["text"]))
        sep = SEP if SEP in text else ALT_SEP if ALT_SEP in text else None
        if kind is None or sep is None:          # no description: a group label (e.g. "Whataboutism"), not an entry
            return
        name, desc = (clean(x) for x in text.split(sep, 1))
        if not name or not desc or len(name) > MAX_NAME:
            return
        top["name"] = name
        parent = next((s["name"] for s in reversed(self._stack[:-1]) if s["name"]), None)
        self.items.append(Fallacy(name, desc, self.h4 or self.h3 or self.h2, kind, top["url"], parent))


def parse_fallacies(html: str) -> list[Fallacy]:
    p = _Parser()
    p.feed(html)
    p.close()
    seen: set[str] = set()
    out = []
    for f in p.items:                          # names are UNIQUE in the table: keep the first occurrence
        if fold(f.name) not in seen:
            seen.add(fold(f.name))
            out.append(f)
    return out


def missing_must_haves(items: list[Fallacy]) -> list[str]:
    names = [fold(f.name) for f in items]
    return [label for label, alts in MUST_HAVE.items() if not any(a in n for a in alts for n in names)]


def lit(v: object) -> str:
    return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"


def to_sql(items: list[Fallacy], oldid: int = OLDID) -> str:
    head = (f"-- Fallacy seed from pt.wikipedia.org 'Lista de falácias', revision {oldid} (CC BY-SA 4.0).\n"
            "-- Generated by deploy/make_fallacies_seed.py; applied only when `fallacies` is EMPTY.\n"
            "INSERT INTO fallacies (name, category, description, wikipedia_url, source, enabled) VALUES\n")
    rows = [f"({lit(FALLACY_NAMES.get(f.name, f.name))}, {lit(f.category)}, {lit(f.description)}, {lit(f.url)}, 'wikipedia', "
            f"{'true' if f.enabled else 'false'})" for f in items]
    return head + ",\n".join(rows) + "\nON CONFLICT (name) DO NOTHING;\n"


def fetch(oldid: int = OLDID) -> dict:
    q = urllib.parse.urlencode({"action": "parse", "oldid": oldid, "prop": "text|sections", "format": "json",
                                "formatversion": 2})
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--from-file", type=Path, help="saved API JSON instead of fetching")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    data = json.loads(args.from_file.read_text(encoding="utf-8")) if args.from_file else fetch()
    parse = data["parse"]
    if parse["revid"] != OLDID:
        print(f"error: revision {parse['revid']} != pinned {OLDID}", file=sys.stderr)
        return 1
    items = parse_fallacies(parse["text"])
    if len(items) < 50:                                   # the page changed shape: refuse to write a bad seed
        print(f"error: only {len(items)} fallacies parsed", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(to_sql(items), encoding="utf-8")
    by: dict[tuple[str, str], int] = {}
    for f in items:
        by[(f.kind, f.category)] = by.get((f.kind, f.category), 0) + 1
    print(f"{len(items)} fallacies -> {args.out} ({sum(f.enabled for f in items)} enabled)")
    for (kind, cat), n in by.items():
        print(f"  {kind:8} {cat}: {n}")
    print("without article link:", sum(f.url is None for f in items))
    print("must-haves missing from the page (add as manual):", missing_must_haves(items) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
