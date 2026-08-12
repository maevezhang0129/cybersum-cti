-- frozen from src/aggregate.py::get_firewall_stats at HEAD, before the Scope refactor.
SELECT 
            COALESCE(raw_data ->> 'clientRequestHTTPHost', 'Unknown') as target_host,
            COALESCE(raw_data ->> 'clientCountryName', 'Unknown') as attacker_country,
            COUNT(*) as block_count
        FROM logs
        WHERE provider = 'cloudflare' 
          AND service = 'firewall'
          AND raw_data ->> 'action' = 'block'
          AND event_timestamp >= NOW() - INTERVAL '24 hours'
          AND raw_data ->> 'clientRequestHTTPHost' IS NOT NULL
          AND raw_data ->> 'clientCountryName' IS NOT NULL
        GROUP BY target_host, attacker_country
        ORDER BY block_count DESC
        LIMIT 5;
