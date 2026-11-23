WITH disabled AS (
    UPDATE django_celery_beat_periodictask
    SET enabled = false
    WHERE enabled AND task IN (
        'auto_move_users_to_base_plan', 'application.tasks.auto_move_users_to_base_plan',
        'nurturing_warm_leads', 'application.tasks.nurturing_warm_leads'
    )
    RETURNING 1
)
INSERT INTO django_celery_beat_periodictasks (ident, last_update)
SELECT 1, statement_timestamp() WHERE EXISTS (SELECT 1 FROM disabled)
ON CONFLICT (ident) DO UPDATE SET last_update = EXCLUDED.last_update;
