# ML Backlog — Olist dataset

## In progress

- **Delivery delay prediction** (regression) — predict delivery time / delay in days.
  Chosen as the first OML4Py project since it's a naturally skewed continuous target,
  a good fit for Yeo-Johnson power transformation, and a well-established theme for
  this dataset. See `ml/delivery_delay/` for the actual work.

## Backlog — revisit after experimenting with OML4Py on delivery delay

- **Review score prediction** — predict `review_score` (1-5) from order/product/seller/
  delivery features. Common theme for this dataset; classification (or ordinal
  regression) rather than the continuous-target case delivery delay covers, so it'd
  exercise different OML4Py algorithms/evaluation metrics.
- **Customer churn / repeat-purchase prediction** — predict whether a customer places
  another order after their first. Needs a different framing of the `customers` table
  (one row per customer, not per order) and a definition of the observation/prediction
  windows — worth scoping properly before starting, not just bolting onto the existing
  per-order tables.
- **Review text NLP** (from Kaggle's own "Inspiration" section) — `order_reviews`
  has a free-text `review_comment_message` field (Portuguese). Sentiment analysis /
  topic modeling on the actual text, distinct from the review-score item above (which
  only uses the numeric 1-5 score as a target, not the text itself). Different tooling
  likely needed — classic OML4 algorithms aren't built for NLP; would probably mean
  embeddings + a classifier, or handing text off to Claude for classification/summarization.
- **Customer clustering / segmentation** (from Kaggle's "Inspiration") — unsupervised:
  characterize customers who never left a review — are they quietly satisfied or quietly
  churned? Genuinely different from everything else on this list since it's unsupervised,
  not a prediction target.
- **Sales / order volume forecasting** (from Kaggle's "Inspiration") — time-series
  forecasting of future order volume from purchase-date history, not a per-order
  regression like delivery delay. Different problem shape (aggregate over time, not
  per-row), would need its own feature table (e.g. daily/weekly order counts).
- **Product-category dissatisfaction** (from Kaggle's "Inspiration") — which categories
  have disproportionately low review scores. Overlaps with the review-score prediction
  item above; could be a byproduct of that model (per-category error/score analysis)
  rather than a separate project.

## UI ideas (front-end developer)

- **Geo map of customer/seller locations** in the Streamlit app — visualize delivery
  distance/delay patterns geographically once the geolocation-based distance feature
  exists in `DELIVERY_DELAY_FEATURES`. Natural fit for `plotly.express` (scatter_geo /
  choropleth on Brazilian states).