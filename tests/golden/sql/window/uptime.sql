-- frozen from evaluation/run_experiment.py::get_uptime_stats_w at HEAD, before the Scope refactor.
WITH latest_scan AS (
            SELECT raw_data
            FROM logs
            WHERE provider = 'uptimerobot'
              AND raw_data ->> 'window_id' = %s
            ORDER BY ingested_at DESC
            LIMIT 1
        )
        SELECT
            monitor ->> 'friendly_name' AS service_name,
            monitor ->> 'url'           AS service_url,
            CASE
                WHEN (monitor ->> 'status')::int = 0 THEN 'Paused'
                WHEN (monitor ->> 'status')::int = 1 THEN 'Not Checked Yet'
                WHEN (monitor ->> 'status')::int = 2 THEN 'Up'
                WHEN (monitor ->> 'status')::int = 8 THEN 'Seems Down'
                WHEN (monitor ->> 'status')::int = 9 THEN 'Down'
                ELSE 'Unknown'
            END AS status_text
        FROM latest_scan,
        jsonb_array_elements(raw_data -> 'monitors') AS monitor
        WHERE (monitor ->> 'status')::int != 2;
