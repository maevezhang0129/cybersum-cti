-- frozen from evaluation/run_experiment.py::get_azure_stats_w at HEAD, before the Scope refactor.
SELECT
            to_char(event_timestamp, 'YYYY-MM-DD HH24:00') AS time_bucket,
            ROUND(AVG(
                CASE
                    WHEN raw_data ->> 'memory_mib' ~ '^\d+\.?\d*$'
                    THEN (raw_data ->> 'memory_mib')::numeric
                    ELSE NULL
                END
            ), 2) AS avg_memory_mb,
            ROUND(MAX(
                CASE
                    WHEN raw_data ->> 'cpu_total_sec' ~ '^\d+\.?\d*$'
                    THEN (raw_data ->> 'cpu_total_sec')::numeric
                    ELSE NULL
                END
            ), 2) AS max_cpu_load
        FROM logs
        WHERE provider = 'azure'
          AND service = 'backend_monitor'
          AND raw_data ->> 'window_id' = %s
        GROUP BY time_bucket
        ORDER BY time_bucket DESC
        LIMIT 24;
