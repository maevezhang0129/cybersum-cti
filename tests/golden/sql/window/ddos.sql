-- frozen from evaluation/run_experiment.py::get_ddos_status_w at HEAD, before the Scope refactor.
SELECT
            event_timestamp,
            raw_data ->> 'health' AS health_status,
            CASE
                WHEN raw_data ->> 'risk_score' ~ '^\d+\.?\d*$'
                THEN (raw_data ->> 'risk_score')::numeric
                ELSE NULL
            END AS risk_score,
            ROUND(
                CASE
                    WHEN raw_data ->> 'malicious_ratio' ~ '^\d+\.?\d*$'
                    THEN (raw_data ->> 'malicious_ratio')::numeric * 100
                    ELSE NULL
                END, 1) AS malicious_percent
        FROM logs
        WHERE provider = 'cloudflare'
          AND service = 'ddos_analyzer'
          AND raw_data ->> 'window_id' = %s
        ORDER BY event_timestamp DESC
        LIMIT 1;
