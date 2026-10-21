-- Manual EDITS in the admin UI may shorten any phrase to 1-10 words, whatever its source.
-- (The 3-10 word rule for EXTRACTED candidates lives in corpus/extract.py; the DB only guards 1-10.)
-- COALESCE: array_length('{}') is NULL and a CHECK passes on NULL, so empty text must be handled explicitly.
ALTER TABLE phrases DROP CONSTRAINT phrase_word_count;
ALTER TABLE phrases ADD CONSTRAINT phrase_word_count CHECK (
    COALESCE(array_length(string_to_array(btrim(text), ' '), 1), 0) BETWEEN 1 AND 10
);
