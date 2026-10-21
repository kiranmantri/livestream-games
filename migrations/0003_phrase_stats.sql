-- Phrase management from the admin page + score learned from real games.
--   prior_score : the hand/corpus-set score (the "prior"); `score` is what card dealing uses and is
--                 recomputed from prior_score + play statistics when a game is finished.
--   dealt/marked: cumulative over finished games (how many cards held the phrase / how many of those
--                 marked it). marked <= dealt because a card holds a phrase at most once.
ALTER TABLE phrases
    ADD COLUMN prior_score real,
    ADD COLUMN dealt  integer NOT NULL DEFAULT 0,
    ADD COLUMN marked integer NOT NULL DEFAULT 0;

UPDATE phrases SET prior_score = score;
ALTER TABLE phrases ALTER COLUMN prior_score SET NOT NULL;

ALTER TABLE phrases
    ADD CONSTRAINT phrase_score_range  CHECK (score >= 0 AND score <= 99),
    ADD CONSTRAINT phrase_prior_range  CHECK (prior_score >= 0 AND prior_score <= 99),
    ADD CONSTRAINT phrase_stats_sane   CHECK (dealt >= 0 AND marked >= 0 AND marked <= dealt);

-- inserts that do not name prior_score (corpus seed, tests, psql) start with prior = score
CREATE FUNCTION phrases_default_prior() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.prior_score IS NULL THEN NEW.prior_score := NEW.score; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER phrases_default_prior BEFORE INSERT ON phrases
    FOR EACH ROW EXECUTE FUNCTION phrases_default_prior();
