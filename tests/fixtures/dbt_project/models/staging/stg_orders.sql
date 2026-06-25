SELECT
    order_id,
    customer_id,
    amount::DECIMAL(10, 2) AS amount,
    status,
    ordered_at::DATE AS order_date
FROM {{ ref('raw_orders') }}
