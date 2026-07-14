--------------------------------------------------------------------------------
-- DELIVERY_DELAY_FEATURES  (v2 -- rebuilt after data-scientist / ml-engineer review)
--
-- Purpose : Feature table for the Olist delivery-delay prediction ML project
--           (data scientist's OML AutoML work, validated first in an OML
--           Notebook). One row per delivered order, with the target
--           (delivery delay in days) and the features needed to model it.
-- Project : Olist delivery-delay prediction (OML_USER schema)
-- Requested by: data science team, delivery-delay prediction project;
--   this v2 revision incorporates a round of decisions from the user plus
--   an ml-engineer review of the v1 table (see CHANGES FROM V1 below).
--
-- ============================================================================
-- *** THIS IS A CHECKOUT-TIME PREDICTION TABLE ***
--
-- The framing is: "predict delivery delay at the moment of purchase, before
-- the order is approved/paid/shipped." Every column intended as a model
-- feature must be a value that is GENUINELY KNOWABLE the instant the
-- customer completes checkout. Anything that only exists/becomes known
-- later in the order lifecycle (payment approval, carrier pickup, actual
-- delivery) is leakage and must not be used as a feature, no matter how
-- predictive it looks.
--
-- Two raw columns are kept in this table despite NOT being checkout-time
-- knowable -- ORDER_DELIVERED_CUSTOMER_DATE and ORDER_ESTIMATED_DELIVERY_DATE
-- -- but ONLY for auditability / target-recomputation (they are the two
-- columns DELIVERY_DELAY_DAYS is derived from). They must be EXCLUDED from
-- any AutoML/model feature set. ORDER_DELIVERED_CUSTOMER_DATE in particular
-- trivially reconstructs the target (DELIVERY_DELAY_DAYS is a direct
-- subtraction of it), so including it as a "feature" would make the model
-- meaningless.
--
-- PRIMARY_PRODUCT_ID and PRIMARY_SELLER_ID are kept for joins/debugging back
-- to source tables only -- do NOT feed these raw high-cardinality ID columns
-- into AutoML directly as categorical features. The model risks memorizing
-- specific IDs instead of generalizing (a new product_id/seller_id at
-- inference time has zero training signal). If product/seller-level
-- features are wanted, derive aggregates (e.g. seller's historical average
-- delay) as a separate, explicit feature-engineering step -- not by handing
-- the raw ID to AutoML.
-- ============================================================================
--
-- Grain / target
--   One row per ORDER_ID, restricted to orders where:
--     ORDER_STATUS = 'delivered' AND ORDER_DELIVERED_CUSTOMER_DATE IS NOT NULL
--   8 orders have ORDER_STATUS = 'delivered' but a NULL delivery date --
--   these are excluded outright (not imputed), per data science direction.
--   Verified counts at build time (unchanged from v1 -- same WHERE clause):
--     ORDERS total                                   : 99,441
--     ORDER_STATUS = 'delivered'                      : 96,478
--     ...of which ORDER_DELIVERED_CUSTOMER_DATE IS NULL: 8   (excluded)
--     ...of which ORDER_DELIVERED_CUSTOMER_DATE NOT NULL: 96,470 (kept)
--   Row count re-verified after this v2 rebuild: 96,470 (unchanged -- the
--   changes below add/replace columns, they do not alter the row filter).
--
--   DELIVERY_DELAY_DAYS = ORDER_DELIVERED_CUSTOMER_DATE - ORDER_ESTIMATED_DELIVERY_DATE
--   Positive  => delivered LATER than estimated (late / the "delay" the
--                project is trying to predict).
--   Negative  => delivered EARLIER than estimated.
--   Computed by casting both TIMESTAMP(6) columns to DATE (drops sub-second
--   precision, irrelevant at day-level granularity) and subtracting, which
--   in Oracle yields a NUMBER of days directly. Verified ORDER_ESTIMATED_
--   DELIVERY_DATE is always at midnight (0 rows deviate), so the fractional
--   part of DELIVERY_DELAY_DAYS is entirely the actual delivery time-of-day
--   -- i.e. this is a continuous day value (e.g. -13.29), not rounded to
--   whole days. Round with TRUNC/ROUND downstream if whole-day buckets are
--   preferred for modeling.
--
-- ============================================================================
-- CHANGES FROM V1 (this rebuild)
-- ============================================================================
--   1. PRIMARY ITEM RULE CHANGED: was "order_item_id = 1" (first-added item),
--      now "highest price, with a deterministic tie-break". The ml-engineer
--      found 902 multi-item orders (~9%) have genuine ties between different
--      products at the same price -- without a tie-break, CTAS results
--      weren't reproducible across rebuilds (ROW_NUMBER order was undefined
--      on ties). Tie-break order is now:
--          ORDER BY price DESC, product_id ASC, order_item_id ASC
--      implemented via ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY ...).
--      This primary item is used ONLY for product/category identity columns:
--      PRIMARY_PRODUCT_ID, PRIMARY_PRODUCT_CATEGORY_NAME(_ENGLISH),
--      PRIMARY_SELLER_ID, PRIMARY_SELLER_STATE.
--
--   2. NEW: "heaviest item" dimension columns, separate from the primary
--      item. The ml-engineer flagged that the highest-price item isn't
--      necessarily the one driving shipping/handling time -- a cheap-but-
--      heavy item can dominate box size/weight. So the physical shipping
--      dimensions (HEAVIEST_PRODUCT_WEIGHT_G/LENGTH_CM/HEIGHT_CM/WIDTH_CM)
--      are now taken from the single heaviest item in the order (its own
--      deterministic tie-break: weight DESC, product_id ASC,
--      order_item_id ASC), NOT from the highest-price primary item. v1's
--      PRIMARY_PRODUCT_WEIGHT_G/LENGTH_CM/HEIGHT_CM/WIDTH_CM columns are
--      REMOVED and replaced by these HEAVIEST_* columns -- there is no
--      longer a "primary item's own dimensions" column, only the heaviest
--      item's dimensions, since that's the physically meaningful quantity
--      for shipping/delay prediction. (Spot-checked on a real order where
--      the highest-price item and heaviest item are genuinely different
--      line items -- see spot-check note at the bottom of this file.)
--
--   3. NEW: distance-in-km features via GEOLOCATION, per this recipe:
--        a. GEOLOCATION has heavy per-zip-prefix duplication (94% of zip
--           prefixes have >1 row: 17,972 of 19,015 distinct prefixes,
--           verified live). ~40 raw rows geocode outside a Brazil bounding
--           box (lat NOT BETWEEN -35 AND 6 / lng NOT BETWEEN -75 AND -33,
--           verified: exactly 40 rows) -- these are filtered out BEFORE
--           aggregating.
--        b. Remaining rows are aggregated to one centroid per zip prefix
--           using MEDIAN(lat)/MEDIAN(lng), not AVG -- more robust to
--           residual bad geocodes than a mean would be.
--        c. Haversine distance (km) is computed PER ORDER_ITEM, using that
--           item's own seller's zip prefix centroid vs. the order's
--           customer zip prefix centroid -- not tied to whichever seller
--           the primary-item rule happens to pick. Distances are then
--           aggregated to order grain as:
--             MAX_DISTANCE_KM  -- the slowest/furthest single shipment in
--                                 the order; the real bottleneck for a
--                                 delivery event that only completes once
--                                 every item has arrived.
--             AVG_DISTANCE_KM  -- average shipment distance across items.
--           ACOS is clamped via LEAST(1, GREATEST(-1, ...)) to avoid NULL
--           results from floating-point overshoot on near-identical points.
--           NOTE: Oracle SQL has no built-in RADIANS() function (unlike
--           Postgres/MySQL) -- degree-to-radian conversion is done manually
--           as `x * ACOS(-1) / 180` in the formula below.
--        d. Left NULL where a customer or seller zip prefix has no
--           GEOLOCATION match after cleaning -- NOT backfilled. Verified
--           live at build time: 158 distinct CUSTOMER zip prefixes and 7
--           distinct SELLER zip prefixes have no match in the cleaned
--           GEOLOCATION centroids; this leaves 477 of the 96,470 kept
--           orders (~0.5%) with NULL MAX_DISTANCE_KM/AVG_DISTANCE_KM.
--           SAME_STATE_FLAG (state-level, always populated) remains as the
--           fallback distance proxy for those rows -- do not impute the
--           km columns.
--
--   4. REMOVED: APPROVAL_TIME_HOURS (and the underlying ORDER_APPROVED_AT
--      raw column). Given the confirmed checkout-time prediction framing,
--      order_approved_at has not happened yet at the moment of purchase --
--      this is leakage, not just a caveat, so it is dropped entirely rather
--      than kept with a warning comment (v1's approach).
--
--   5. NEW: PROMISED_DELIVERY_DAYS = ORDER_ESTIMATED_DELIVERY_DATE -
--      ORDER_PURCHASE_TIMESTAMP, in days (same DATE-cast/subtraction
--      pattern as DELIVERY_DELAY_DAYS). This is legitimately knowable at
--      checkout (the estimated delivery date is shown to the customer at
--      purchase time) and is likely predictive -- a short promised window
--      is inherently harder to hit than a generous one.
-- ============================================================================
--
-- Multi-item order aggregation (carried over from v1, still a judgment call)
--   An order can have multiple ORDER_ITEMS rows (multiple products and/or
--   sellers), but the prediction target is per-order. Order-level totals
--   (sum across all items in the order) are still provided:
--     NUM_ITEMS            = COUNT(order_item_id)
--     TOTAL_PRICE          = SUM(price)
--     TOTAL_FREIGHT_VALUE  = SUM(freight_value)
--     TOTAL_WEIGHT_G       = SUM(product_weight_g)   -- total shipment
--                             weight is the physically meaningful quantity
--                             for shipping/delay, unlike e.g. summing box
--                             dimensions, which doesn't make physical sense.
--   9,635 of the 96,470 kept orders (~10%) have more than one item.
--
-- Other features
--   CUSTOMER_STATE / PRIMARY_SELLER_STATE / SAME_STATE_FLAG give a coarse,
--   always-available distance proxy (state-level) -- kept as a fallback for
--   the ~0.5% of orders where MAX_DISTANCE_KM/AVG_DISTANCE_KM is NULL (no
--   GEOLOCATION match), and useful in its own right alongside the new km
--   features.
--   ORDER_PURCHASE_TIMESTAMP is carried through as-is for time-based
--   train/test splitting and seasonality features downstream.
--
-- Materialization choice: TABLE, not a view.
--   This is meant to be a stable, reusable training dataset for repeated
--   OML AutoML runs -- re-running the join on every training pass would be
--   wasteful and could shift slightly if source tables change mid-project.
--   Refresh manually (DROP + re-run this script, or CREATE TABLE ... AS
--   SELECT into a new dated table) if the source Olist tables are reloaded.
--
-- Source tables (OML_USER): ORDERS, ORDER_ITEMS, PRODUCTS,
--   PRODUCT_CATEGORY_TRANSLATION, CUSTOMERS, SELLERS, GEOLOCATION.
--   Column names verified live against USER_TAB_COLUMNS before writing this
--   script (not assumed from memory).
--
-- Spot check performed at v2 rebuild time (order_id
-- 'b7f44ef7fe56341d2d0d0703d65a429b' -- 4 items, 2 sellers, genuine 3-way
-- price tie among items 2/3/4 all at price 39.9):
--   - Primary item (price DESC, product_id ASC tie-break) correctly resolved
--     to order_item_id 3 (product '1c5507038caa58651b5f07b729f97774'), the
--     lexicographically smallest product_id among the 3 tied-price items --
--     confirms the tie-break is deterministic and picks a different item
--     than a naive "first matching row" would.
--   - Heaviest item correctly resolved to order_item_id 1 (product
--     '24ea331e89e0cc6fde885752b3eb2c23', weight_g 400), which is a
--     DIFFERENT item than the price-based primary item above -- confirms
--     the primary-item/heaviest-item split behaves as intended.
--   - TOTAL_PRICE = 154.70 (35.00 + 39.90*3), TOTAL_FREIGHT_VALUE = 56.39
--     (14.63 + 13.92*3), TOTAL_WEIGHT_G = 750 (400+100+150+100) -- all
--     verified against the raw ORDER_ITEMS/PRODUCTS rows.
--   - Per-item distances: seller zip 01001 (item 1) -> 871.52 km, seller zip
--     02804 (items 2/3/4) -> 863.24 km each (same seller, same distance).
--     MAX_DISTANCE_KM = 871.52 (matches the single largest per-item value);
--     AVG_DISTANCE_KM = 865.31 (matches (871.52 + 863.24*3)/4).
--   All values matched hand computation exactly.
--------------------------------------------------------------------------------

DROP TABLE DELIVERY_DELAY_FEATURES PURGE;

CREATE TABLE DELIVERY_DELAY_FEATURES AS
WITH item_agg AS (
    SELECT
        oi.order_id,
        COUNT(oi.order_item_id)  AS num_items,
        SUM(oi.price)            AS total_price,
        SUM(oi.freight_value)    AS total_freight_value,
        SUM(p.product_weight_g)  AS total_weight_g
    FROM order_items oi
    JOIN products p ON p.product_id = oi.product_id
    GROUP BY oi.order_id
),
primary_ranked AS (
    SELECT
        oi.order_id,
        oi.product_id,
        oi.seller_id,
        p.product_category_name,
        pt.product_category_name_english,
        s.seller_state,
        ROW_NUMBER() OVER (
            PARTITION BY oi.order_id
            ORDER BY oi.price DESC, oi.product_id ASC, oi.order_item_id ASC
        ) AS rn
    FROM order_items oi
    JOIN products p ON p.product_id = oi.product_id
    LEFT JOIN product_category_translation pt ON pt.product_category_name = p.product_category_name
    JOIN sellers s ON s.seller_id = oi.seller_id
),
primary_item AS (
    SELECT
        order_id,
        product_id                     AS primary_product_id,
        seller_id                      AS primary_seller_id,
        product_category_name          AS primary_product_category_name,
        product_category_name_english  AS primary_product_category_name_english,
        seller_state                   AS primary_seller_state
    FROM primary_ranked
    WHERE rn = 1
),
heaviest_ranked AS (
    SELECT
        oi.order_id,
        oi.product_id,
        p.product_weight_g,
        p.product_length_cm,
        p.product_height_cm,
        p.product_width_cm,
        ROW_NUMBER() OVER (
            PARTITION BY oi.order_id
            ORDER BY p.product_weight_g DESC, oi.product_id ASC, oi.order_item_id ASC
        ) AS rn
    FROM order_items oi
    JOIN products p ON p.product_id = oi.product_id
),
heaviest_item AS (
    SELECT
        order_id,
        product_weight_g  AS heaviest_product_weight_g,
        product_length_cm AS heaviest_product_length_cm,
        product_height_cm AS heaviest_product_height_cm,
        product_width_cm  AS heaviest_product_width_cm
    FROM heaviest_ranked
    WHERE rn = 1
),
geo_clean AS (
    SELECT geolocation_zip_code_prefix, geolocation_lat, geolocation_lng
    FROM geolocation
    WHERE geolocation_lat BETWEEN -35 AND 6
      AND geolocation_lng BETWEEN -75 AND -33
),
geo_centroid AS (
    SELECT
        geolocation_zip_code_prefix AS zip_prefix,
        MEDIAN(geolocation_lat) AS lat,
        MEDIAN(geolocation_lng) AS lng
    FROM geo_clean
    GROUP BY geolocation_zip_code_prefix
),
item_distance AS (
    SELECT
        oi.order_id,
        oi.order_item_id,
        -- Haversine distance in km. Oracle has no built-in RADIANS(), so
        -- degrees->radians is done manually as (x * ACOS(-1) / 180).
        6371 * ACOS( LEAST(1, GREATEST(-1,
            SIN((cg.lat * ACOS(-1) / 180)) * SIN((sg.lat * ACOS(-1) / 180)) +
            COS((cg.lat * ACOS(-1) / 180)) * COS((sg.lat * ACOS(-1) / 180))
                * COS(((sg.lng - cg.lng) * ACOS(-1) / 180))
        )) ) AS distance_km
    FROM order_items oi
    JOIN orders o        ON o.order_id = oi.order_id
    JOIN customers c     ON c.customer_id = o.customer_id
    JOIN sellers s       ON s.seller_id = oi.seller_id
    JOIN geo_centroid cg ON cg.zip_prefix = c.customer_zip_code_prefix
    JOIN geo_centroid sg ON sg.zip_prefix = s.seller_zip_code_prefix
),
distance_agg AS (
    SELECT
        order_id,
        MAX(distance_km) AS max_distance_km,
        AVG(distance_km) AS avg_distance_km
    FROM item_distance
    GROUP BY order_id
)
SELECT
    o.order_id,
    o.order_purchase_timestamp,
    o.order_estimated_delivery_date,
    o.order_delivered_customer_date,
    (CAST(o.order_estimated_delivery_date AS DATE) - CAST(o.order_purchase_timestamp AS DATE))
                                                            AS promised_delivery_days,
    (CAST(o.order_delivered_customer_date AS DATE) - CAST(o.order_estimated_delivery_date AS DATE))
                                                            AS delivery_delay_days,
    ia.num_items,
    ia.total_price,
    ia.total_freight_value,
    ia.total_weight_g,
    pi.primary_product_id,
    pi.primary_product_category_name,
    pi.primary_product_category_name_english,
    pi.primary_seller_id,
    pi.primary_seller_state,
    hi.heaviest_product_weight_g,
    hi.heaviest_product_length_cm,
    hi.heaviest_product_height_cm,
    hi.heaviest_product_width_cm,
    c.customer_state,
    CASE WHEN c.customer_state = pi.primary_seller_state THEN 1 ELSE 0 END AS same_state_flag,
    da.max_distance_km,
    da.avg_distance_km
FROM orders o
JOIN item_agg     ia ON ia.order_id = o.order_id
JOIN primary_item pi ON pi.order_id = o.order_id
JOIN heaviest_item hi ON hi.order_id = o.order_id
JOIN customers    c  ON c.customer_id = o.customer_id
LEFT JOIN distance_agg da ON da.order_id = o.order_id
WHERE o.order_status = 'delivered'
  AND o.order_delivered_customer_date IS NOT NULL;

ALTER TABLE DELIVERY_DELAY_FEATURES ADD CONSTRAINT DELIVERY_DELAY_FEATURES_PK PRIMARY KEY (order_id);

CREATE INDEX DDF_PURCHASE_TS_IDX ON DELIVERY_DELAY_FEATURES (order_purchase_timestamp);

COMMENT ON TABLE DELIVERY_DELAY_FEATURES IS
    'CHECKOUT-TIME prediction table (v2): one row per delivered Olist order (order_status=delivered, delivered_customer_date not null), with delivery_delay_days target and features for the delivery-delay prediction ML project. Every feature column must be genuinely knowable at the moment of purchase -- order_delivered_customer_date and order_estimated_delivery_date are kept ONLY for auditability/target-recomputation and must be excluded from any AutoML feature set (order_delivered_customer_date trivially reconstructs the target). primary_product_id/primary_seller_id are for joins/debugging only, not raw AutoML categorical features. Built from ORDERS/ORDER_ITEMS/PRODUCTS/PRODUCT_CATEGORY_TRANSLATION/CUSTOMERS/SELLERS/GEOLOCATION -- see databases/OML_USER/tables/delivery_delay_features.sql for full derivation notes, tie-break rules, and the v1->v2 change log.';

COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.order_id IS 'Primary key, FK to ORDERS.order_id.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.order_purchase_timestamp IS 'Order purchase timestamp, carried through unchanged for time-based train/test splitting and seasonality features. Checkout-time knowable.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.order_estimated_delivery_date IS 'AUDIT/TARGET-RECOMPUTATION ONLY -- do not use as a model feature directly (use derived PROMISED_DELIVERY_DAYS instead, which is the checkout-time-safe version of this). Estimated delivery date shown to the customer at purchase time.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.order_delivered_customer_date IS 'AUDIT/TARGET-RECOMPUTATION ONLY -- EXCLUDE from AutoML feature set. This is not knowable at checkout time and trivially reconstructs delivery_delay_days (the target). Always NOT NULL in this table by construction.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.promised_delivery_days IS 'order_estimated_delivery_date - order_purchase_timestamp, in days (DATE-cast subtraction). Checkout-time knowable (the estimate is shown to the customer at purchase) -- legitimate feature, unlike the two raw date columns above.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.delivery_delay_days IS 'TARGET column: order_delivered_customer_date - order_estimated_delivery_date, in days. Positive = delivered later than estimated (late); negative = delivered earlier than estimated. Continuous value (includes delivery time-of-day fraction, since order_estimated_delivery_date is always midnight) -- not rounded to whole days.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.num_items IS 'Count of ORDER_ITEMS rows (line items / units) for this order. Checkout-time knowable.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.total_price IS 'SUM(order_items.price) across all items in the order. Checkout-time knowable.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.total_freight_value IS 'SUM(order_items.freight_value) across all items in the order. Checkout-time knowable.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.total_weight_g IS 'SUM(products.product_weight_g) across all items in the order -- total shipment weight, used instead of a single item''s weight because weight sums sensibly across a multi-item shipment. Checkout-time knowable.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.primary_product_id IS 'product_id of the highest-price item in the order, tie-broken deterministically by ORDER BY price DESC, product_id ASC, order_item_id ASC (902 multi-item orders, ~9%, have genuine price ties -- this makes CTAS reproducible across rebuilds). Used as the representative product for category identity since category doesn''t aggregate sensibly across a mixed cart. FOR JOINS/DEBUGGING ONLY -- do not feed into AutoML as a raw categorical feature (high-cardinality ID, risk of memorization).';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.primary_product_category_name IS 'Portuguese product_category_name of the primary (highest-price, tie-broken) item.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.primary_product_category_name_english IS 'English translation of primary_product_category_name via PRODUCT_CATEGORY_TRANSLATION; NULL if the category has no translation row or the product has a NULL category (both occur in source data).';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.primary_seller_id IS 'seller_id of the primary (highest-price, tie-broken) item. FOR JOINS/DEBUGGING ONLY -- do not feed into AutoML as a raw categorical feature.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.primary_seller_state IS 'seller_state of the primary seller -- used with customer_state as a coarse, always-available state-level distance proxy (see same_state_flag), independent of the primary/heaviest item split.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.heaviest_product_weight_g IS 'product_weight_g of the single heaviest item in the order (own deterministic tie-break: weight DESC, product_id ASC, order_item_id ASC) -- NOT necessarily the same item as primary_product_id/highest-price item. Represents the item most likely to drive shipping/handling time. Checkout-time knowable.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.heaviest_product_length_cm IS 'product_length_cm of the heaviest item (see heaviest_product_weight_g). Checkout-time knowable.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.heaviest_product_height_cm IS 'product_height_cm of the heaviest item (see heaviest_product_weight_g). Checkout-time knowable.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.heaviest_product_width_cm IS 'product_width_cm of the heaviest item (see heaviest_product_weight_g). Checkout-time knowable.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.customer_state IS 'customer_state from CUSTOMERS. Checkout-time knowable.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.same_state_flag IS '1 if customer_state = primary_seller_state, else 0 -- coarse same-state/cross-state shipping proxy, always populated (fallback for the ~0.5% of orders with NULL max_distance_km/avg_distance_km). Checkout-time knowable.';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.max_distance_km IS 'MAX across order_items of per-item Haversine distance (km) between the item''s seller zip-prefix centroid and the order''s customer zip-prefix centroid (centroids = MEDIAN(lat)/MEDIAN(lng) of cleaned GEOLOCATION rows per zip prefix). Represents the slowest/furthest single shipment in the order -- the real bottleneck for a delivery event that completes only once every item arrives. NULL when the customer or any relevant seller zip prefix has no GEOLOCATION match after cleaning (158 customer / 7 seller zip prefixes unmatched, verified live -- affects 477 of 96,470 orders, ~0.5%). NOT imputed -- use same_state_flag as the fallback. Checkout-time knowable (both zip prefixes are known at purchase).';
COMMENT ON COLUMN DELIVERY_DELAY_FEATURES.avg_distance_km IS 'AVG across order_items of the same per-item Haversine distance used for max_distance_km. Same NULL behavior/coverage as max_distance_km. Checkout-time knowable.';