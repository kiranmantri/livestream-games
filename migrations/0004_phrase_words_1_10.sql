-- Word limits per source: MANUAL phrases 1-10 words; extracted (transcript / chat / both) 3-10 words.
-- COALESCE: array_length('{}') is NULL and a CHECK passes on NULL, so an empty text used to slip through.
ALTER TABLE phrases DROP CONSTRAINT phrase_word_count;
ALTER TABLE phrases ADD CONSTRAINT phrase_word_count CHECK (
    COALESCE(array_length(string_to_array(btrim(text), ' '), 1), 0) BETWEEN
        (CASE WHEN source = 'manual' THEN 1 ELSE 3 END) AND 10
);
