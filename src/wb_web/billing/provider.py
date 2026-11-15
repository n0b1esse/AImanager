"""PaymentProvider abstraction.

Local market reality (KG) is handled by the ManualProvider: invoices/accounts are
paid out-of-band and an operator activates the subscription by hand. Real
acquirer adapters (MBank/Finik etc.) plug in here later without touching callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CheckoutResult:
    provider: str
    url: str | None
    instructions: str | None


class PaymentProvider(Protocol):
    name: str

    async def create_checkout(self, *, user_id: int, plan: str, months: int) -> CheckoutResult: ...

    async def cancel(self, *, user_id: int, external_id: str | None) -> bool: ...


class ManualProvider:
    """No online processing: operator activates after the money lands."""

    name = "manual"

    async def create_checkout(self, *, user_id: int, plan: str, months: int) -> CheckoutResult:
        return CheckoutResult(
            provider=self.name,
            url=None,
            instructions=(
                f"Оплата по реквизитам у оператора поддержки. "
                f"План: {plan} · {months} мес. После поступления оператор активирует подписку вручную. "
                f"Укажите ваш email в назначении платежа."
            ),
        )

    async def cancel(self, *, user_id: int, external_id: str | None) -> bool:
        return True


_PROVIDERS: dict[str, PaymentProvider] = {ManualProvider.name: ManualProvider()}


def get_provider(name: str) -> PaymentProvider:
    try:
        return _PROVIDERS[name]
    except KeyError as exc:
        raise KeyError(f"payment provider '{name}' is not configured") from exc
