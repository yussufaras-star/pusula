"""Banka / fintech API adaptörleri. Her modülün fetch(hesap, başlangıç, http) → AdapterResult."""

from pusula.finance.providers import garanti, kuveytturk, mercury, revolut, stripe, wise
from pusula.finance.providers.base import Adapter

ADAPTERS: dict[str, Adapter] = {
    "mercury": mercury.fetch,
    "wise": wise.fetch,
    "stripe": stripe.fetch,
    "revolut": revolut.fetch,
    "kuveytturk": kuveytturk.fetch,
    "garanti": garanti.fetch,
}
