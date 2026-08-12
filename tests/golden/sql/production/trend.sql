-- frozen from src/aggregate.py::get_90day_trend at HEAD, before the Scope refactor.
WITH daily_stats AS (
            SELECT 
                TO_CHAR(event_timestamp, 'MM-DD') as day_label,  
                raw_data ->> 'action' as action,                 
                COUNT(*) as action_count
            FROM logs
            WHERE provider = 'cloudflare'                       
              AND service = 'firewall'                          
              AND event_timestamp >= NOW() - INTERVAL '90 days'  
              AND raw_data ->> 'action' IS NOT NULL            
            GROUP BY day_label, action
        ),
        ranked_actions AS (
            SELECT 
                day_label,
                action,
                action_count,
                ROW_NUMBER() OVER (PARTITION BY day_label ORDER BY action_count DESC) as rn
            FROM daily_stats
        )
        SELECT 
            ds.day_label as d,               
            SUM(ds.action_count) as c,       
            MAX(ra.action) as t              
        FROM daily_stats ds
        JOIN ranked_actions ra ON ds.day_label = ra.day_label AND ra.rn = 1
        GROUP BY ds.day_label
        ORDER BY ds.day_label ASC;
