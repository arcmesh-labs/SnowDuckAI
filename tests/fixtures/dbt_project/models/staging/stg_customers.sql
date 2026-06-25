SELECT
    customer_id,
    customer_name,
    email,
    created_at::DATE AS created_date
FROM {{ ref('raw_customers') }}
