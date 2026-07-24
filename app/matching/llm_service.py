"""
LLM adjudication service — Phase 1 of LLM_INTEGRATION_PLAN.md.

A thin, provider-abstracted client that asks a language model one question:
are these two hotel records the same physical property? It exists to resolve
the borderline cases the rule scorer and the embedding layer cannot reason
about — rebrands ("Clarks Inn" -> "DS Clarks Inn"), transliterations
(Mamallaa / Mahabalipuram), and chain-branch collisions (three "Lemon Tree
Premier" in one city).

Three constraints, all inherited from the pipeline's safety posture:

  * Feature-flagged. With settings.LLM_ENABLED False every method is a no-op
    that returns an `unavailable` verdict, so the caller falls through to its
    existing decision and behaviour is identical to today.
  * Degrades, never merges. Any failure — disabled, timeout, API error, a
    refusal, an unparseable response — returns ok=False. A broken LLM can only
    ever send a record to MANUAL_REVIEW; it can never produce a merge.
  * Adjudicator, not authority. This module returns a verdict. Whether that
    verdict is *allowed to act* — and only ever within the borderline band,
    never overriding a rule rejection — is the caller's decision (Phase 2+),
    gated by settings.LLM_MIN_CONFIDENCE.

Provider: OpenAI (gpt-4o-mini by default). The rest of the pipeline talks to
this class, not to the SDK, so swapping providers is a change confined here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from pydantic import BaseModel, Field

from config import settings

logger = logging.getLogger("uvicorn")

# The SDK is optional until LLM_ENABLED is turned on, so a missing package must
# not break import for a pipeline running with the flag off.
try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - exercised only without the dep installed
    AsyncOpenAI = None


# Per-1M-token prices (USD, input, output), for cost tracking only. An unknown
# model falls back to the gpt-4o-mini rate so the estimate never raises.
_PRICE_PER_1M = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
}
_DEFAULT_PRICE = (0.15, 0.60)


class HotelMatchDecision(BaseModel):
    """The shape the model must return — enforced via OpenAI structured outputs.

    Every field is required and unconstrained by design: structured outputs
    reject numeric/length constraints, so the 0.0-1.0 range on `confidence` is
    stated in the description and clamped by the caller, not by the schema.
    """

    same_hotel: bool = Field(
        description="True if the two records describe the same physical property."
    )
    confidence: float = Field(
        description="Certainty of the same_hotel verdict, from 0.0 to 1.0."
    )
    reasoning: str = Field(
        description="One or two sentences naming the deciding evidence."
    )


@dataclass
class LLMVerdict:
    """What the service hands back. `ok` is the load-bearing field: when False,
    the caller must ignore same_hotel/confidence and keep its existing decision.
    """

    same_hotel: bool | None
    confidence: float
    reasoning: str
    ok: bool
    error: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0

    @classmethod
    def unavailable(cls, reason: str) -> "LLMVerdict":
        """A verdict the caller must treat as 'no opinion' — falls back to review."""
        return cls(
            same_hotel=None,
            confidence=0.0,
            reasoning="",
            ok=False,
            error=reason,
        )


# ── Prompts ──────────────────────────────────────────────────────────────────
# The domain knowledge is the same whether we are comparing a supplier record to
# a master or two masters to each other, so it lives in one place. It is stable
# across every call, which also lets OpenAI's automatic prompt caching amortise
# it once the prompt crosses the cache threshold.
_DOMAIN_KNOWLEDGE = """\
Decide on identity, not string similarity. Apply this domain knowledge:

- Hotels rebrand. The same building can change operator or brand name
  (ITC -> "Mementos by ITC"; "Clarks Inn" -> "DS Clarks Inn"). A changed brand
  at the same location is usually one property, not two.
- City and place names vary across suppliers and transliterations
  (Bangalore/Bengaluru, Calicut/Kozhikode, Mamallapuram/Mahabalipuram/Mamallaa).
  Treat these as equivalent.
- Coordinates from different geocoders for one hotel can disagree by 1-3 km,
  especially when an address resolves to a locality centroid. Distance alone
  neither confirms nor rules out identity.
- Chains run multiple branches in one city ("Lemon Tree Premier" has several in
  Delhi). Same brand name but a different address or area = different properties.
- A shared building/plot number with the same street or postcode is strong
  evidence of one property; different primary building numbers on the same road
  are strong evidence of two.
- Word order and generic words ("Hotel", "The", "Inn", "Resort") carry little
  identity; the distinctive tokens do.

Be decisive when the evidence is clear and cautious when it conflicts. Report a
high confidence only when the deciding evidence is unambiguous; when the records
genuinely could go either way, say so with a low confidence — a human handles
those."""

_MATCH_SYSTEM_PROMPT = (
    "You are an expert hotel-data analyst deciding whether two hotel records "
    "from different travel suppliers describe the SAME physical property.\n\n"
    + _DOMAIN_KNOWLEDGE
)

_DUPLICATE_SYSTEM_PROMPT = (
    "You are an expert hotel-data analyst deciding whether two consolidated "
    "master hotel records are in fact the SAME physical property — a duplicate "
    "that should be merged.\n\n"
    + _DOMAIN_KNOWLEDGE
)


def _record_lines(record: dict, label: str) -> str:
    """Render a hotel record as a compact, aligned block for the prompt."""

    def value(*keys):
        for key in keys:
            found = record.get(key)
            if found not in (None, ""):
                return found
        return "—"

    return (
        f"{label}:\n"
        f"  name:        {value('hotel_name', 'name')}\n"
        f"  address:     {value('address', 'normalized_address')}\n"
        f"  city:        {value('city')}\n"
        f"  state:       {value('state')}\n"
        f"  postal_code: {value('postal_code')}\n"
        f"  coordinates: {value('latitude')}, {value('longitude')}\n"
        f"  star_rating: {value('star_rating')}"
    )


def _distance_phrase(distance_meters) -> str:
    if distance_meters is None:
        return "The distance between them is unknown."
    return f"They are approximately {float(distance_meters):.0f} m apart."


class LLMService:
    """Stateless adjudicator over the OpenAI API.

    Constructed with an optional `session` purely so it instantiates like the
    other services (``LLMService(session)``); it needs no database of its own.
    Also runs standalone: ``LLMService()``.
    """

    def __init__(self, session=None):
        self.session = session
        self._client = None
        # Running totals for cost tracking; read via usage_summary().
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def enabled(self) -> bool:
        return bool(settings.LLM_ENABLED)

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if AsyncOpenAI is None:
            raise RuntimeError(
                "The `openai` package is not installed; add it (see "
                "requirements.txt) or keep LLM_ENABLED off."
            )
        kwargs = {
            "max_retries": settings.LLM_MAX_RETRIES,
            "timeout": settings.LLM_TIMEOUT_SECONDS,
        }
        # Blank key → the SDK resolves OPENAI_API_KEY from the environment.
        if settings.LLM_API_KEY:
            kwargs["api_key"] = settings.LLM_API_KEY
        self._client = AsyncOpenAI(**kwargs)
        return self._client

    # ── Public API ────────────────────────────────────────────────────────────

    async def verify_hotel_match(
        self, supplier_hotel: dict, master_hotel: dict, distance_meters=None
    ) -> LLMVerdict:
        """Are a supplier record and a candidate master the same hotel?"""
        user_prompt = (
            "Are these two records the same physical hotel?\n\n"
            f"{_record_lines(supplier_hotel, 'Record A (supplier record)')}\n\n"
            f"{_record_lines(master_hotel, 'Record B (existing master)')}\n\n"
            f"{_distance_phrase(distance_meters)}"
        )
        return await self._adjudicate(_MATCH_SYSTEM_PROMPT, user_prompt)

    async def classify_duplicate_masters(
        self, master_a: dict, master_b: dict, distance_meters=None
    ) -> LLMVerdict:
        """Are two existing master records the same hotel (a duplicate to merge)?"""
        user_prompt = (
            "Are these two master records the same physical hotel?\n\n"
            f"{_record_lines(master_a, 'Master A')}\n\n"
            f"{_record_lines(master_b, 'Master B')}\n\n"
            f"{_distance_phrase(distance_meters)}"
        )
        return await self._adjudicate(_DUPLICATE_SYSTEM_PROMPT, user_prompt)

    async def verify_hotel_match_batch(self, pairs) -> list[LLMVerdict]:
        """Verify many pairs concurrently, bounded by LLM_BATCH_CONCURRENCY.

        `pairs` is an iterable of ``(supplier_hotel, master_hotel)`` or
        ``(supplier_hotel, master_hotel, distance_meters)`` tuples. Results are
        returned in the same order.
        """
        pairs = list(pairs)
        if not self.enabled:
            return [LLMVerdict.unavailable("LLM disabled") for _ in pairs]

        semaphore = asyncio.Semaphore(max(1, settings.LLM_BATCH_CONCURRENCY))

        async def run(pair):
            supplier, master = pair[0], pair[1]
            distance = pair[2] if len(pair) > 2 else None
            async with semaphore:
                return await self.verify_hotel_match(supplier, master, distance)

        return await asyncio.gather(*(run(pair) for pair in pairs))

    def usage_summary(self) -> dict:
        """Token counts and an estimated dollar cost for everything run so far."""
        in_price, out_price = _PRICE_PER_1M.get(settings.LLM_MODEL, _DEFAULT_PRICE)
        cost = self.input_tokens / 1e6 * in_price + self.output_tokens / 1e6 * out_price
        return {
            "provider": settings.LLM_PROVIDER,
            "model": settings.LLM_MODEL,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": round(cost, 4),
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    async def _adjudicate(self, system_prompt: str, user_prompt: str) -> LLMVerdict:
        if not self.enabled:
            return LLMVerdict.unavailable("LLM disabled")

        try:
            client = self._ensure_client()
        except RuntimeError as exc:
            logger.warning("LLM unavailable: %s", exc)
            return LLMVerdict.unavailable(str(exc))

        request = {
            "model": settings.LLM_MODEL,
            "max_tokens": settings.LLM_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": HotelMatchDecision,
        }
        # gpt-4o-mini accepts temperature; leave it None to omit for models that
        # reject sampling params.
        if settings.LLM_TEMPERATURE is not None:
            request["temperature"] = settings.LLM_TEMPERATURE

        # Structured-output parse is GA at chat.completions.parse in openai>=1.47;
        # older 1.x exposes it only under beta. Prefer GA, fall back to beta.
        parse = getattr(client.chat.completions, "parse", None)
        if parse is None:
            parse = client.beta.chat.completions.parse

        started = time.perf_counter()
        try:
            completion = await parse(**request)
        except Exception as exc:  # noqa: BLE001 - any failure must degrade to review
            logger.warning(
                "LLM call failed (%s); falling back to review", exc.__class__.__name__
            )
            return LLMVerdict.unavailable(f"{exc.__class__.__name__}: {exc}")
        latency_ms = (time.perf_counter() - started) * 1000

        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            return LLMVerdict.unavailable(f"model refused: {message.refusal}")

        decision = message.parsed
        if decision is None:
            return LLMVerdict.unavailable("model returned no parseable decision")

        usage = getattr(completion, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        self.calls += 1
        self.input_tokens += in_tok
        self.output_tokens += out_tok

        # Clamp confidence into range — the schema can't, and callers compare it
        # against LLM_MIN_CONFIDENCE.
        confidence = max(0.0, min(1.0, float(decision.confidence)))

        return LLMVerdict(
            same_hotel=bool(decision.same_hotel),
            confidence=confidence,
            reasoning=decision.reasoning,
            ok=True,
            model=settings.LLM_MODEL,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=round(latency_ms, 1),
        )
