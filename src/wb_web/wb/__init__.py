"""Wildberries API integration (ported from desktop client, desktop-grade contract)."""

from wb_web.wb.client import (
    COMMON_API_BASE,
    FEEDBACKS_API_BASE,
    FEEDBACKS_SANDBOX_BASE,
    RetryPolicy,
    WildberriesClient,
)

__all__ = [
    "COMMON_API_BASE",
    "FEEDBACKS_API_BASE",
    "FEEDBACKS_SANDBOX_BASE",
    "RetryPolicy",
    "WildberriesClient",
]
