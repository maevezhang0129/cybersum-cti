-- frozen from src/aggregate.py::get_uptime_stats at HEAD, before the Scope refactor.
WITH latest_scan AS (
            SELECT raw_data 
            FROM logs 
            WHERE provider = 'uptimerobot' 
            ORDER BY ingested_at DESC 
            LIMIT 1
        )
        SELECT 
            monitor ->> 'friendly_name' as service_name,
            monitor ->> 'url' as service_url,
            CASE 
                WHEN (monitor ->> 'status')::int = 0 THEN 'Paused'
                WHEN (monitor ->> 'status')::int = 1 THEN 'Not Checked Yet'
                WHEN (monitor ->> 'status')::int = 2 THEN 'Up'
                WHEN (monitor ->> 'status')::int = 8 THEN 'Seems Down'
                WHEN (monitor ->> 'status')::int = 9 THEN 'Down'
                ELSE 'Unknown'
            END as status_text
        FROM latest_scan,
        jsonb_array_elements(raw_data -> 'monitors') as monitor
        WHERE (monitor ->> 'status')::int != 2;
