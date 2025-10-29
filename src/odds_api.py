"""
Authenticated client and aggregation helpers for The Odds API.

This module fetches match odds, converts them to implied probabilities,
normalises them to remove bookmaker margins, and provides summaries that
can be consumed by CLI tools or other services.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests
from dateutil import parser as date_parser
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_BOOKMAKERS: Sequence[str] = (
    "bet365",
    "paddypower",
    "williamhill",
    "ladbrokes",
    "betfair",
    "skybet",
    "marathonbet",
    "unibet",
    "betvictor",
    "pinnacle",
)

LEAGUE_TO_SPORT_KEY: Dict[str, str] = {
    "epl": "soccer_epl",
    "la_liga": "soccer_spain_la_liga",
    "bundesliga": "soccer_germany_bundesliga",
}


def _create_retry_session(
    retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: Optional[Sequence[int]] = None,
) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist or (429, 500, 502, 503, 504),
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def implied_probability(decimal_odds: Optional[float]) -> Optional[float]:
    if decimal_odds is None:
        return None
    if decimal_odds <= 0:
        return None
    return 1.0 / float(decimal_odds)


def normalize_probabilities(
    home: Optional[float],
    draw: Optional[float],
    away: Optional[float],
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    probs = [p for p in (home, draw, away) if p is not None]
    if not probs:
        return home, draw, away
    total = sum(probs)
    if total <= 0:
        return home, draw, away
    normalized_home = home / total if home is not None else None
    normalized_draw = draw / total if draw is not None else None
    normalized_away = away / total if away is not None else None
    return normalized_home, normalized_draw, normalized_away


@dataclass(frozen=True)
class SourceProbabilities:
    source: str
    home: Optional[float]
    draw: Optional[float]
    away: Optional[float]
    last_update: Optional[datetime]


@dataclass(frozen=True)
class MatchProbabilityReport:
    league: str
    sport_key: str
    match_id: str
    commence_time: datetime
    home_team: str
    away_team: str
    sources: List[SourceProbabilities]
    average_home: Optional[float]
    average_draw: Optional[float]
    average_away: Optional[float]
    recommendation: Optional[str]
    recommendation_confidence: Optional[float]


class TheOddsAPIClient:
    BASE_URL = "https://api.the-odds-api.com/v4"

    def __init__(
        self,
        api_key: str,
        *,
        session: Optional[requests.Session] = None,
        timeout: int = 10,
    ) -> None:
        if not api_key:
            raise ValueError("API key is required.")
        self.api_key = api_key
        self.session = session or _create_retry_session()
        self.timeout = timeout

    def _request(self, path: str, params: Optional[Dict[str, str]] = None) -> List[Dict]:
        query = dict(params or {})
        query["apiKey"] = self.api_key
        response = self.session.get(
            f"{self.BASE_URL}{path}",
            params=query,
            timeout=self.timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"The Odds API request failed ({response.status_code}): {response.text}"
            )
        return response.json()

    def get_odds(
        self,
        sport_key: str,
        *,
        regions: str = "uk,eu",
        markets: str = "h2h",
        odds_format: str = "decimal",
        date_format: str = "iso",
        bookmakers: Optional[Iterable[str]] = None,
    ) -> List[Dict]:
        params: Dict[str, str] = {
            "regions": regions,
            "markets": markets,
            "oddsFormat": odds_format,
            "dateFormat": date_format,
        }
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)
        return self._request(f"/sports/{sport_key}/odds/", params=params)


class OddsAggregator:
    def __init__(
        self,
        client: TheOddsAPIClient,
        *,
        league_map: Optional[Dict[str, str]] = None,
        bookmakers: Optional[Sequence[str]] = None,
        regions: str = "uk,eu",
        markets: str = "h2h",
        odds_format: str = "decimal",
        date_format: str = "iso",
    ) -> None:
        self.client = client
        self.league_map = league_map or dict(LEAGUE_TO_SPORT_KEY)
        self.bookmakers = bookmakers or DEFAULT_BOOKMAKERS
        self.regions = regions
        self.markets = markets
        self.odds_format = odds_format
        self.date_format = date_format

    def fetch_league(self, league: str) -> List[MatchProbabilityReport]:
        if league not in self.league_map:
            raise KeyError(f"Unsupported league '{league}'.")
        sport_key = self.league_map[league]
        events = self.client.get_odds(
            sport_key,
            regions=self.regions,
            markets=self.markets,
            odds_format=self.odds_format,
            date_format=self.date_format,
            bookmakers=self.bookmakers,
        )
        return self._build_reports(league, sport_key, events)

    def fetch_many(self, leagues: Sequence[str]) -> List[MatchProbabilityReport]:
        reports: List[MatchProbabilityReport] = []
        for league in leagues:
            reports.extend(self.fetch_league(league))
        return reports

    def _build_reports(
        self,
        league: str,
        sport_key: str,
        events: Sequence[Dict],
    ) -> List[MatchProbabilityReport]:
        reports: List[MatchProbabilityReport] = []
        for event in events:
            match_id = event.get("id")
            home_team = event.get("home_team")
            away_team = event.get("away_team")
            raw_start = event.get("commence_time")
            if not all([match_id, home_team, away_team, raw_start]):
                continue
            commence_time = date_parser.isoparse(raw_start)

            sources: List[SourceProbabilities] = []
            for bookmaker in event.get("bookmakers", []):
                market = next(
                    (m for m in bookmaker.get("markets", []) if m.get("key") == "h2h"),
                    None,
                )
                if not market:
                    continue

                home_prob, draw_prob, away_prob = None, None, None
                for outcome in market.get("outcomes", []):
                    name = outcome.get("name")
                    price = outcome.get("price")
                    prob = implied_probability(price)
                    if name == home_team:
                        home_prob = prob
                    elif name == away_team:
                        away_prob = prob
                    elif isinstance(name, str) and name.lower() in {"draw", "tie"}:
                        draw_prob = prob

                home_prob, draw_prob, away_prob = normalize_probabilities(
                    home_prob, draw_prob, away_prob
                )

                last_update_raw = market.get("last_update")
                last_update = (
                    date_parser.isoparse(last_update_raw) if last_update_raw else None
                )
                sources.append(
                    SourceProbabilities(
                        source=bookmaker.get("key", "unknown"),
                        home=home_prob,
                        draw=draw_prob,
                        away=away_prob,
                        last_update=last_update,
                    )
                )

            avg_home, avg_draw, avg_away = self._averages(sources)
            recommendation, confidence = self._recommend(avg_home, avg_draw, avg_away)
            reports.append(
                MatchProbabilityReport(
                    league=league,
                    sport_key=sport_key,
                    match_id=str(match_id),
                    commence_time=commence_time,
                    home_team=str(home_team),
                    away_team=str(away_team),
                    sources=sources,
                    average_home=avg_home,
                    average_draw=avg_draw,
                    average_away=avg_away,
                    recommendation=recommendation,
                    recommendation_confidence=confidence,
                )
            )
        return reports

    @staticmethod
    def _averages(
        sources: Sequence[SourceProbabilities],
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        if not sources:
            return None, None, None

        def mean(values: Sequence[float]) -> Optional[float]:
            return sum(values) / len(values) if values else None

        home_vals = [s.home for s in sources if s.home is not None]
        draw_vals = [s.draw for s in sources if s.draw is not None]
        away_vals = [s.away for s in sources if s.away is not None]

        return mean(home_vals), mean(draw_vals), mean(away_vals)

    @staticmethod
    def _recommend(
        avg_home: Optional[float],
        avg_draw: Optional[float],
        avg_away: Optional[float],
    ) -> Tuple[Optional[str], Optional[float]]:
        options = {
            "home": avg_home,
            "draw": avg_draw,
            "away": avg_away,
        }
        valid = {k: v for k, v in options.items() if v is not None}
        if len(valid) < 2:
            if not valid:
                return None, None
            outcome, _ = next(iter(valid.items()))
            return outcome, None
        sorted_outcomes = sorted(valid.items(), key=lambda kv: kv[1], reverse=True)
        best_outcome, best_val = sorted_outcomes[0]
        second_val = sorted_outcomes[1][1]
        return best_outcome, best_val - second_val


def load_client_from_env(
    env_var: str = "THE_ODDS_API_KEY",
) -> TheOddsAPIClient:
    api_key = os.getenv(env_var)
    if not api_key:
        raise RuntimeError(
            f"Environment variable '{env_var}' is not set. Export your API key first."
        )
    return TheOddsAPIClient(api_key)
