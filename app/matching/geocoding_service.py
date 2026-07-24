"""
Geocoding validation — cross-check supplier coordinates against a geocoding
provider (B1 in LLM_INTEGRATION_PLAN.md).

Bad coordinates are the single biggest source of duplicate masters: two records
for one hotel whose coordinates disagree by more than the candidate-search
radius never get compared, and silently become two masters (the Mementos-by-ITC
at 1608 m case). This service geocodes `name + address + city + …` and measures
how far the supplied coordinate sits from where the provider places the hotel —
so a suspect coordinate can be flagged (and later corrected) rather than quietly
fragmenting the master list.

Built on geopy (already a dependency). Defaults to Nominatim (OpenStreetMap),
which needs no API key; set GEOCODING_PROVIDER=google + GEOCODING_API_KEY for
Google's higher-quality India coverage. Off by default (GEOCODING_ENABLED), and
purely a *measurement* — it reads coordinates and reports, it never writes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from geopy.distance import geodesic

from config import settings

logger = logging.getLogger("uvicorn")


@dataclass
class GeoResult:
    latitude: float
    longitude: float
    matched_address: str | None = None
    raw: dict | None = None


class GeocodingService:
    """Async-friendly wrapper over a geopy geocoder, with rate limiting so the
    public Nominatim server's 1-request/second policy is respected.
    """

    def __init__(self, session=None):
        # `session` is accepted so it constructs like the other services; the
        # geocoder itself needs no database.
        self.session = session
        self._geolocator = None
        self._last_call_at = 0.0
        self.calls = 0

    @property
    def enabled(self) -> bool:
        return bool(settings.GEOCODING_ENABLED)

    def _ensure_geolocator(self):
        if self._geolocator is not None:
            return self._geolocator

        provider = (settings.GEOCODING_PROVIDER or "nominatim").lower()
        timeout = settings.GEOCODING_TIMEOUT_SECONDS

        if provider == "google":
            from geopy.geocoders import GoogleV3

            if not settings.GEOCODING_API_KEY:
                raise RuntimeError("GEOCODING_PROVIDER=google requires GEOCODING_API_KEY.")
            self._geolocator = GoogleV3(api_key=settings.GEOCODING_API_KEY, timeout=timeout)
        elif provider == "nominatim":
            from geopy.geocoders import Nominatim

            # Nominatim's usage policy requires a descriptive User-Agent.
            self._geolocator = Nominatim(
                user_agent=settings.GEOCODING_USER_AGENT, timeout=timeout
            )
        else:
            raise RuntimeError(
                f"Unknown GEOCODING_PROVIDER {provider!r}; use 'nominatim' or 'google'."
            )

        return self._geolocator

    async def _respect_rate_limit(self):
        interval = settings.GEOCODING_MIN_INTERVAL_SECONDS
        if interval and interval > 0:
            wait = self._last_call_at + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
        self._last_call_at = time.monotonic()

    @staticmethod
    def _candidate_queries(name, address, city, state, postal_code, country) -> list[str]:
        """Queries to try, most- to least-specific. The full address goes first
        because Google resolves it precisely; Nominatim tends to miss on long
        strings, so the name-led queries are the fallback that catches those.
        """

        def joined(*parts) -> str | None:
            out = []
            for piece in parts:
                if piece is None:
                    continue
                text = str(piece).strip()
                if text and text.lower() != "nan":
                    out.append(text)
            return ", ".join(out) if out else None

        raw = [
            joined(name, address, city, state, postal_code, country),
            joined(name, city, state, country),
            joined(name, city, country),
            joined(address, city, country),
        ]
        seen, queries = set(), []
        for query in raw:
            if query and query not in seen:
                seen.add(query)
                queries.append(query)
        return queries

    async def geocode(
        self,
        name=None,
        address=None,
        city=None,
        state=None,
        postal_code=None,
        country=None,
    ) -> GeoResult | None:
        """Look the hotel up. Returns None when disabled, unresolvable, or the
        provider errors — callers treat None as 'no external opinion'.
        """
        if not self.enabled:
            return None

        queries = self._candidate_queries(name, address, city, state, postal_code, country)
        if not queries:
            return None

        try:
            geolocator = self._ensure_geolocator()
        except RuntimeError as exc:
            logger.warning("geocoding unavailable: %s", exc)
            return None

        # First hit wins. Most records resolve on the first (fullest) query —
        # one call; only a miss escalates to the name-led fallbacks.
        for query in queries:
            await self._respect_rate_limit()
            try:
                location = await asyncio.to_thread(geolocator.geocode, query)
            except Exception as exc:  # noqa: BLE001 - timeouts/service errors -> next query / None
                logger.warning("geocode failed for %r: %s", query, exc.__class__.__name__)
                continue
            self.calls += 1
            if location is not None:
                return GeoResult(
                    latitude=float(location.latitude),
                    longitude=float(location.longitude),
                    matched_address=getattr(location, "address", None),
                    raw=getattr(location, "raw", None),
                )
        return None

    async def validate(self, supplied_lat, supplied_lng, **fields) -> dict:
        """Geocode the hotel and measure how far the supplied coordinate is from
        the result. Verdict is OK / SUSPECT / UNGEOCODABLE.
        """
        result = await self.geocode(**fields)
        if result is None:
            return {"geocoded": None, "mismatch_meters": None, "verdict": "UNGEOCODABLE"}

        mismatch = None
        if supplied_lat is not None and supplied_lng is not None:
            try:
                mismatch = geodesic(
                    (float(supplied_lat), float(supplied_lng)),
                    (result.latitude, result.longitude),
                ).meters
            except (TypeError, ValueError):
                mismatch = None

        verdict = "OK"
        if mismatch is not None and mismatch > settings.GEOCODING_MISMATCH_METERS:
            verdict = "SUSPECT"

        return {"geocoded": result, "mismatch_meters": mismatch, "verdict": verdict}
