"""End-to-end smoke test against a RUNNING stack (e.g. docker compose). Skipped unless SMOKE_URL is set.

    SMOKE_URL=http://127.0.0.1:8000 SMOKE_ADMIN_PASSWORD=... uv run pytest tests/e2e -q

Needs a stack with NO active game and >= 24 enabled phrases. It plays one full game (start, join, mark, finish)
and leaves one ended session behind.
"""
from __future__ import annotations

import os
import re

import httpx
import pytest

URL = os.environ.get("SMOKE_URL", "").rstrip("/")
PW = os.environ.get("SMOKE_ADMIN_PASSWORD", "")
pytestmark = pytest.mark.skipif(not URL or not PW, reason="set SMOKE_URL and SMOKE_ADMIN_PASSWORD")

CELL = re.compile(r'hx-post="/mark/(\d+)"')


def client(**kw) -> httpx.Client:
    return httpx.Client(base_url=URL, follow_redirects=True, timeout=10, **kw)


def hidden(html: str, name: str) -> str:
    m = re.search(rf'name="{name}"[^>]*value="([^"]+)"|value="([^"]+)"[^>]*name="{name}"', html)
    assert m, f"hidden field {name} not found"
    return m.group(1) or m.group(2)


def test_full_game_flow():
    admin = ("admin", PW)
    with client() as anon:
        assert anon.get("/healthz").text == "ok"                                   # app + DB reachable
        assert anon.get("/admin").status_code == 401
        assert anon.get("/admin", auth=("admin", PW + "x")).status_code == 401
        assert anon.get("/admin", auth=admin).status_code == 200
        assert anon.get("/app.css").status_code == 200 and anon.get("/htmx.min.js").status_code == 200
        assert anon.get("/center.jpg").status_code == 200

    with client() as ana, client() as bia:
        entry = ana.get("/").text
        assert "Começar partida" in entry, "a game is already active: finish it first (reset the stack)"
        home = ana.post("/start", data={"name": "Ana"}).text                       # first visitor starts the game
        assert len(CELL.findall(home)) == 24 and "/center.jpg" in home             # 5x5 card, avatar in the centre
        assert "lg_token" in ana.cookies

        assert "Entrar na partida" in bia.get("/").text                            # others join, never start another
        assert "Começar partida" not in bia.get("/").text
        dup = bia.post("/join", data={"name": "ANA"}).text
        assert "já está em uso" in dup                                             # unique name, case-insensitive
        assert len(CELL.findall(bia.post("/join", data={"name": "Bia"}).text)) == 24

        marked = ana.post("/mark/3").text                                          # mark -> HH:MM:SS shown
        assert re.search(r'class="at"[^>]*>\d\d:\d\d:\d\d<', marked)
        assert re.search(r'class="at"', ana.get("/").text), "mark must survive a reload"
        assert ana.post("/mark/12").status_code == 400                             # the centre cell is not markable
        bia.post("/mark/1")
        bia.post("/mark/1")                                                        # toggled off again

        page = httpx.get(f"{URL}/admin", auth=admin, timeout=10).text
        assert "Ana" in page and "Bia" in page
        fin = httpx.post(f"{URL}/admin/finish", auth=admin, timeout=10, follow_redirects=True,
                         data={"session_id": hidden(page, "session_id"), "csrf": hidden(page, "csrf")})
        assert fin.status_code == 200 and "Partida encerrada" in fin.text

        assert "Fim de jogo!" in ana.get("/").text                                 # players see the results screen
        assert "Ana" in ana.get("/").text                                          # Ana: 1 mark vs Bia: 0 -> winner

    phrases = httpx.get(f"{URL}/admin/phrases", auth=admin, timeout=10)
    assert phrases.status_code == 200 and 'name="ids"' in phrases.text
