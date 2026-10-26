-- 0014: the host's decision and the AI check are independent (bug: a host score set status='resolved', so the AI never ran or its answer was lost).
-- status now describes ONLY the AI pipeline: queued -> running -> done | needs_admin.
-- The host's decision: resolved_at IS NOT NULL (admin_score = the host's score; NULL = dismissed, nobody scores). The host's score wins for points.

ALTER TABLE fact_checks DROP CONSTRAINT fact_check_admin_score_only_resolved;
ALTER TABLE fact_checks DROP CONSTRAINT fact_check_resolved_time;
ALTER TABLE fact_checks DROP CONSTRAINT IF EXISTS fact_checks_status_check;

-- rows the host already decided get back the AI state they really have (an unanswered one goes back to the queue: the AI runs if the game is active)
UPDATE fact_checks SET status = CASE
        WHEN verdict IS NOT NULL AND (confidence = 'low' OR verdict = 'unverifiable') THEN 'needs_admin'
        WHEN verdict IS NOT NULL THEN 'done'
        WHEN error IS NOT NULL AND attempts >= 3 THEN 'needs_admin'
        ELSE 'queued' END
    WHERE status = 'resolved';

ALTER TABLE fact_checks ADD CONSTRAINT fact_checks_status_check CHECK (status IN ('queued', 'running', 'done', 'needs_admin'));
ALTER TABLE fact_checks ADD CONSTRAINT fact_check_host_score_needs_decision CHECK (admin_score IS NULL OR resolved_at IS NOT NULL);
