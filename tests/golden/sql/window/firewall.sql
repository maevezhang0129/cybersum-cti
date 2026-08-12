-- frozen from evaluation/run_experiment.py::get_firewall_stats_w at HEAD, before the Scope refactor.
SELECT
            COALESCE(raw_data ->> 'clientRequestHTTPHost', 'Unknown') AS target_host,
            COALESCE(raw_data ->> 'clientCountryName', 'Unknown') AS attacker_country,
            COUNT(*) AS block_count
        FROM logs
        WHERE provider = 'cloudflare'
          AND service = 'firewall'
          AND raw_data ->> 'action' = 'block'
          AND raw_data ->> 'window_id' = %s
        GROUP BY target_host, attacker_country
        ORDER BY block_count DESC
        LIMIT 5;
