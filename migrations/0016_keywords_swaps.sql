-- Bingo: per-phrase review (app/marks.py) + secret keywords and card swaps (app/swaps.py).
-- The admin picks up to 3 keywords among the phrases on the game's cards (phrase ids as on the cards' snapshots: no FK, a
-- phrase deleted from the list stays a keyword of the running game). A confirmed mark of a keyword earns its player one swap.
CREATE TABLE bingo_keywords (
    session_id uuid        NOT NULL REFERENCES game_sessions (id) ON DELETE CASCADE,
    phrase_id  bigint      NOT NULL,
    chosen_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, phrase_id)
);

-- Phrases the admin confirmed (Correto) in a game: every mark of it is confirmed, also marks made later.
CREATE TABLE confirmed_phrases (
    session_id   uuid        NOT NULL REFERENCES game_sessions (id) ON DELETE CASCADE,
    phrase_id    bigint      NOT NULL,
    confirmed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, phrase_id)
);

-- One swap per player and keyword. card_id + cell_index = the confirmed mark that earned it: an Incorreto on that mark (or
-- the card going away with a kicked player, sessions.kick_player) takes an UNUSED swap back. used_at + target_id = the swap was made with that
-- player (SET NULL if they are kicked later: the log keeps the swap); declined_at = the holder said no.
CREATE TABLE swap_rights (
    id          bigserial   PRIMARY KEY,
    session_id  uuid        NOT NULL REFERENCES game_sessions (id) ON DELETE CASCADE,
    user_id     uuid        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    phrase_id   bigint      NOT NULL,
    card_id     uuid        REFERENCES bingo_cards (id) ON DELETE SET NULL,   -- NULL: that card went away with a kicked player
    cell_index  smallint    NOT NULL,
    earned_at   timestamptz NOT NULL DEFAULT now(),
    used_at     timestamptz,
    declined_at timestamptz,
    target_id   uuid        REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT one_swap_per_keyword UNIQUE (session_id, user_id, phrase_id),
    CONSTRAINT swap_closed_once CHECK (used_at IS NULL OR declined_at IS NULL)
);

-- Cards stay immutable, except inside a swap transaction (SET LOCAL livegames.card_swap = 'on'), which may exchange the
-- CELLS of two cards; the row's id, session and owner never change (marks are moved by the swap itself).
CREATE OR REPLACE FUNCTION bingo_cards_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('livegames.card_swap', true) = 'on'
       AND NEW.id = OLD.id AND NEW.session_id = OLD.session_id AND NEW.user_id = OLD.user_id THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'bingo_cards rows are immutable once assigned';
END $$;
