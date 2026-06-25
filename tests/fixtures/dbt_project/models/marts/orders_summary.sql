SELECT
    c.customer_id,
    c.customer_name,
    c.customer_segment,
    COUNT(o.order_id)          AS order_count,
    SUM(o.amount)              AS total_amount,
    MAX(o.order_date)          AS last_order_date
FROM {{ ref('stg_customers') }} c
LEFT JOIN {{ ref('stg_orders') }} o
    ON c.customer_id = o.customer_id
GROUP BY 1, 2, 3
