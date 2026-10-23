-- A pair can carry a free-form message from the player (read by the admin in the review queue).
ALTER TABLE fallacy_marks ADD COLUMN note text NOT NULL DEFAULT '' CONSTRAINT mark_note_len CHECK (char_length(note) <= 280);
