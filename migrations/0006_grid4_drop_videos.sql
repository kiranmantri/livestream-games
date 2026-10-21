-- 1) Corpus provenance is no longer stored in the database.
DROP TABLE ingested_videos;

-- 2) Card size is a property of the game session. Sessions that already exist keep 5x5 (their cards are
--    immutable 25-cell snapshots); every NEW session defaults to 4x4. All players of a session share its size.
ALTER TABLE game_sessions
    ADD COLUMN grid_size smallint NOT NULL DEFAULT 5,
    ADD CONSTRAINT session_grid_size CHECK (grid_size IN (4, 5));
ALTER TABLE game_sessions ALTER COLUMN grid_size SET DEFAULT 4;

-- 3) A card has 16 cells (4x4: all phrases) or 25 (5x5: phrases + the avatar in the centre, index 12).
ALTER TABLE bingo_cards DROP CONSTRAINT bingo_cards_cells_check;
ALTER TABLE bingo_cards ADD CONSTRAINT card_cell_count
    CHECK (jsonb_typeof(cells) = 'array' AND jsonb_array_length(cells) IN (16, 25));

-- 4) Marks: index 12 is a normal cell on a 4x4 card, so "not 12" can no longer be a plain CHECK.
--    Rule moved into a trigger: a mark must point at a PHRASE cell of its own card (the avatar and
--    out-of-range cells are rejected, whatever the grid).
ALTER TABLE bingo_marks DROP CONSTRAINT bingo_marks_cell_index_check;
ALTER TABLE bingo_marks ADD CONSTRAINT mark_index_range CHECK (cell_index BETWEEN 0 AND 24);

CREATE FUNCTION bingo_marks_phrase_cell_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM bingo_cards c
                   WHERE c.id = NEW.card_id AND jsonb_exists(c.cells -> NEW.cell_index::int, 'phrase_id')) THEN
        RAISE EXCEPTION 'cell % of card % is not a phrase cell', NEW.cell_index, NEW.card_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER bingo_marks_phrase_cell BEFORE INSERT ON bingo_marks
    FOR EACH ROW EXECUTE FUNCTION bingo_marks_phrase_cell_only();

-- 5) Admin "unmark": a mark the admin removed is also BLOCKED, so the player cannot simply tap it again.
--    The admin can lift the block. Rows disappear with their card.
CREATE TABLE revoked_marks (
    card_id    uuid        NOT NULL REFERENCES bingo_cards (id) ON DELETE CASCADE,
    cell_index smallint    NOT NULL CHECK (cell_index BETWEEN 0 AND 24),
    revoked_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (card_id, cell_index)
);
