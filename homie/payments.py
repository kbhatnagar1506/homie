"""Getting paid when the renter gets their keys.

Relay users: a Relay payment card (Stripe checkout; money settles to the org's Stripe account).
ASI:One users: the Fetch Payment Protocol, with the FET transfer verified on the Fetch mainnet before we confirm.
"""

import asyncio
import logging

from homie.config import env

log = logging.getLogger("homie.payments")
AFET_PER_FET = 10**18


def fee_usd_cents() -> int:
    return int(round(float(env("HOMIE_FEE_USD", "1.00")) * 100))


def fee_fet() -> str:
    return env("HOMIE_FEE_AMOUNT", "0.1")


async def verify_fet_transfer(tx_hash: str, recipient: str, min_fet: float) -> tuple[bool, str]:
    """Look the transaction up on Fetch mainnet: did at least min_fet go to recipient, successfully?"""
    from cosmpy.aerial.client import LedgerClient, NetworkConfig

    def check() -> tuple[bool, str]:
        tx = LedgerClient(NetworkConfig.fetchai_mainnet()).query_tx(tx_hash)
        if tx.code != 0:
            return False, f"transaction failed on-chain (code {tx.code})"
        received = 0
        for kind, attrs in (tx.events or {}).items():
            if kind != "transfer":
                continue
            recipients, amounts = attrs.get("recipient"), attrs.get("amount")
            pairs = zip(recipients if isinstance(recipients, list) else [recipients],
                        amounts if isinstance(amounts, list) else [amounts])
            for to, amount in pairs:
                if to == recipient and amount and str(amount).endswith("afet"):
                    received += int(str(amount)[:-4])
        if received >= int(min_fet * AFET_PER_FET):
            return True, f"verified {received / AFET_PER_FET:g} FET at height {tx.height}"
        return False, f"only {received / AFET_PER_FET:g} FET reached Homie"

    try:
        return await asyncio.to_thread(check)
    except Exception as e:
        return False, f"couldn't find that transaction yet ({str(e)[:80]})"


async def relay_card(relay, chat_id: str, description: str) -> str:
    """Send a Relay payment card. Returns 'sent', or why it couldn't."""
    import uuid

    try:
        req = await relay.payment_requests.create(description=description[:32], category="physical_goods",
                                                  amount=fee_usd_cents(), currency="usd", idempotency_key=uuid.uuid4().hex)
    except Exception as e:
        log.warning("Relay payment request failed: %s", e)
        return "stripe-not-connected" if "Stripe" in str(e) else "failed"
    await relay.chats.messages.send(chat_id, {"message": {"parts": [{"type": "payment", "checkout_url": req["checkout_url"]}],
                                                          "idempotency_key": uuid.uuid4().hex}})
    return "sent"
