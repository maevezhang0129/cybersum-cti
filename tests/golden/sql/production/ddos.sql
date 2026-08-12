-- frozen from src/aggregate.py::get_ddos_status at HEAD, before the Scope refactor.
SELECT 
            event_timestamp,
            raw_data ->> 'health' as health_status,
            CASE 
                WHEN raw_data ->> 'risk_score' ~ '^\d+\.?\d*$'
                THEN (raw_data ->> 'risk_score')::numeric
                ELSE NULL
            END as risk_score,
            ROUND(
                CASE 
                    WHEN raw_data ->> 'malicious_ratio' ~ '^\d+\.?\d*$'
                    THEN (raw_data ->> 'malicious_ratio')::numeric * 100
                    ELSE NULL
                END, 1) as malicious_percent
        FROM logs
        WHERE provider = 'cloudflare' AND service = 'ddos_analyzer'
        ORDER BY event_timestamp DESC 
        LIMIT 1;
