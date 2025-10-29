"""
Minimal client for fetching active markets from Polymarket.

The public API is undocumented and may change; this implementation targets the
`/api/markets` endpoint exposed on polymarket.com. Update the endpoint or JSON
parsing logic if Polymarket revises their APIs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import requests

from .text_utils import normalize_text

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://polymarket.com/api/markets"


def _normalize_text(value: str) -> str:
    return normalize_text(value)


@dataclass(frozen=True)
class PolymarketMarket:
    id: str
    question: str
    slug: str
    active: bool
    closed: bool
    start_time: Optional[str]
    end_time: Optional[str]

    @property
    def normalized_question(self) -> str:
        return _normalize_text(self.question)


class PolymarketClient:
    def __init__(self, base_url: str = DEFAULT_ENDPOINT, *, timeout: int = 10) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def get_active_markets(
        self,
        *,
        limit: int = 1000,
        include_closed: bool = False,
    ) -> List[PolymarketMarket]:
        params = {
            "limit": str(limit),
            "closed": "false" if not include_closed else "true",
        }
        response = requests.get(
            self.base_url,
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

        # Some responses wrap markets in {'markets': [...]}; others return a list
        markets_raw = data.get("markets", data)

        markets: List[PolymarketMarket] = []
        for item in markets_raw:
            try:
                market = PolymarketMarket(
                    id=str(item.get("id") or item.get("_id") or item.get("market_id")),
                    question=str(item.get("question") or item.get("title") or ""),
                    slug=str(item.get("slug") or ""),
                    active=bool(item.get("active", True)),
                    closed=bool(item.get("closed", False)),
                    start_time=item.get("startDate") or item.get("start_time"),
                    end_time=item.get("endDate") or item.get("end_time"),
                )
            except Exception as exc:
                logger.debug("Skipping malformed market payload %s: %s", item, exc)
                continue
            if not market.question:
                continue
            if include_closed or (market.active and not market.closed):
                markets.append(market)
        return markets


def match_reports_to_polymarket(
    reports: Sequence["MatchProbabilityReport"],
    markets: Sequence[PolymarketMarket],
) -> Dict[str, PolymarketMarket]:
    """Return mapping of match_id -> PolymarketMarket if the fixture exists on Polymarket."""
    normalized_markets = {
        market.normalized_question: market for market in markets
    }

    mapping: Dict[str, PolymarketMarket] = {}

    for report in reports:
        home_norm = _normalize_text(report.home_team)
        away_norm = _normalize_text(report.away_team)
        candidates = (
            f"{home_norm} vs {away_norm}",
            f"{away_norm} vs {home_norm}",
            f"{home_norm} v {away_norm}",
            f"{away_norm} v {home_norm}",
        )
        matched_market = None
        for candidate in candidates:
            for question, market in normalized_markets.items():
                if home_norm in question and away_norm in question:
                    if candidate in question or home_norm in question and away_norm in question:
                        matched_market = market
                        break
            if matched_market:
                break

        if matched_market:
            mapping[report.match_id] = matched_market

    return mapping
