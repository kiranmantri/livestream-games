"""YouTube's own named emoji (the blue/purple/green faces, hands, ...) arrive in chat as image emoji with a `:name:` shortcut and
no Unicode character. Panes are plain text, so each one is mapped to the closest Unicode emoji. A YouTube-style name that is not in
the table is dropped (a raw `:face-x-y:` in the middle of a message is worse than nothing); any other `:shortcut:` is a channel
emoji and stays as typed."""
from __future__ import annotations

import re

import psycopg

# whole-name matches first (no colour/body-part prefix pattern)
EXACT = {
    "elbowcough": "🤧",
    "yougotthis": "💪",
    "washhands": "🧼",
}
# the tail of a `<group>-<colour>-<tail>` name. Longest tails are tried first.
TAILS = {
    "smiling": "😊", "covering-eyes": "🙈", "raised-eyebrow": "🤨", "crying": "😢", "raised-arms": "🙌",
    "heart-shape": "😍", "tongue-out": "😝", "drinking-coffee": "☕", "wide-eyes": "😳", "biting-nails": "😬",
    "droopy-eyes": "🥱", "question-mark": "🤔", "poop-shape": "💩", "waving": "👋", "game-over": "🎮",
    "whistling": "😗", "yellow-diamond": "😎", "heart-red": "🫶",
}
# the group decides when the tail alone is ambiguous: "eyes-purple-crying" is sobbing, "cat-...-whistling" is a cat
GROUP_TAILS = {("eyes", "crying"): "😭", ("cat", "whistling"): "🐱", ("person", "waving"): "👋",
               ("glasses", "yellow-diamond"): "😎", ("hands", "heart-red"): "🫶"}
GROUPS = ("face", "body", "hand", "hands", "eyes", "person", "cat", "text", "glasses")

SHORTCODE = re.compile(r":([a-z0-9]+(?:-[a-z0-9]+)*):")


def _lookup(name: str) -> str | None:
    """Emoji for a YouTube-style name, '' when the name looks like one but is unknown, None when it is something else."""
    if name in EXACT:
        return EXACT[name]
    group, _, rest = name.partition("-")
    if group not in GROUPS or not rest:
        return None
    parts = rest.split("-")
    for i in range(len(parts)):                    # drop the colour word(s), keep the longest known tail
        tail = "-".join(parts[i:])
        if (group, tail) in GROUP_TAILS:
            return GROUP_TAILS[(group, tail)]
        if tail in TAILS:
            return TAILS[tail]
    return ""


def replace_shortcodes(text: str) -> str:
    def sub(m: re.Match) -> str:
        found = _lookup(m.group(1))
        return m.group(0) if found is None else found
    return re.sub(r"\s+", " ", SHORTCODE.sub(sub, text)).strip()


def fix_replay_events(conn: psycopg.Connection, video_id: str | None = None) -> int:
    """One-off for rows imported before the mapping existed: rewrites replay_events.text. Idempotent. Returns rows changed.
    Rows already copied into a game (stream_events) are NOT touched: stored quote offsets refer to that text."""
    rows = conn.execute("SELECT id, text FROM replay_events WHERE text ~ ':[a-z0-9-]+:' AND (%s::text IS NULL OR video_id = %s)",
                        (video_id, video_id)).fetchall()
    changed = 0
    for rid, text in rows:
        new = replace_shortcodes(text)
        if new == text or not new:
            continue
        try:
            with conn.transaction():
                conn.execute("UPDATE replay_events SET text = %s WHERE id = %s", (new, rid))
            changed += 1
        except psycopg.errors.UniqueViolation:     # same offset, same text after mapping: keep the original row
            pass
    return changed
