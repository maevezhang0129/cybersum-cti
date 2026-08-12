-- frozen from src/aggregate.py::get_azure_stats at HEAD, before the Scope refactor.
SELECT 
            to_char(event_timestamp, 'YYYY-MM-DD HH24:00') as time_bucket,
            ROUND(AVG(
                CASE 
                    WHEN raw_data ->> 'memory_mib' ~ '^\d+\.?\d*$' 
                    THEN (raw_data ->> 'memory_mib')::numeric 
                    ELSE NULL 
                END
            ), 2) as avg_memory_mb,
            ROUND(MAX(
                CASE 
                    WHEN raw_data ->> 'cpu_total_sec' ~ '^\d+\.?\d*$'
                    THEN (raw_data ->> 'cpu_total_sec')::numeric
                    ELSE NULL
                END
            ), 2) as max_cpu_load
        FROM logs
        WHERE provider = 'azure' AND service = 'backend_monitor'
          AND event_timestamp >= NOW() - INTERVAL '24 hours'
        GROUP BY time_bucket
        ORDER BY time_bucket DESC
        LIMIT 24;
