# Live Games

Web app with games to play during a live stream (channel: @O_Poligrafo). First game: Bingo,
with cells drawn from phrases mined from past stream transcripts and chat.

See `AGENTS.md` for conventions, `docs/architecture.md` for what runs where (with a diagram), `docs/operations.md` for
backup / restore / deploy, and `docs/design.md` for the original design (historical).

## Dev quick start
    cp .env.example .env          # then edit secrets
    uv sync
    docker compose up -d db
