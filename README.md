# Live Games

Web app with games to play during a live stream (channel: @O_Poligrafo). First game: Bingo,
with cells drawn from phrases mined from past stream transcripts and chat.

See `AGENTS.md` for conventions and `docs/design.md` for the design.

## Dev quick start
    cp .env.example .env          # then edit secrets
    uv sync
    docker compose up -d db
