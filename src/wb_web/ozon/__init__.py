"""Ozon Seller API integration (Client-Id + Api-Key).

Contract verified against docs.ozon.ru on 2026-11-16:
- POST {base}/v2/review/list — list reviews; filters{status, sku, ...}, last_id pagination,
  limit 20..100; requires "Управление отзывами"/Premium Pro subscription
- POST {base}/v1/review/comment/list — comments on one review (seller check = has our comment)
- POST {base}/v1/review/comment/create — seller comment; mark_review_as_processed marks Processed

Non-goals (for now): questions (вопросы-ответы), analytics, ratings.
"""
