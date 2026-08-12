-- frozen from evaluation/run_experiment.py::get_90day_trend_w at HEAD, before the Scope refactor.
WITH daily_stats AS (
            SELECT
                TO_CHAR(event_timestamp, 'MM-DD') AS day_label,
                raw_data ->> 'action'             AS action,
                COUNT(*) AS action_count
            FROM logs
            WHERE provider = 'cloudflare'
              AND service = 'firewall'
              AND raw_data ->> 'window_id' = %s
              AND raw_data ->> 'action' IS NOT NULL
            GROUP BY day_label, action
        ),
        ranked_actions AS (
            SELECT
                day_label, action, action_count,
                ROW_NUMBER() OVER (
                    PARTITION BY day_label ORDER BY action_count DESC
                ) AS rn
            FROM daily_stats
        )
        SELECT
            ds.day_label AS d,
            SUM(ds.action_count) AS c,
            MAX(ra.action) AS t
        FROM daily_stats ds
        JOIN ranked_actions ra
          ON ds.day_label = ra.day_label AND ra.rn = 1
        GROUP BY ds.day_label
        ORDER BY ds.day_label ASC;
