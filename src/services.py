"""
Service layer for grouping odds data by league, day, and match.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence

from .odds_api import MatchProbabilityReport, OddsAggregator, TheOddsAPIClient
from .text_utils import short_team_code


@dataclass
class MatchOddsSummary:
    report: MatchProbabilityReport

    @property
    def match_id(self) -> str:
        return self.report.match_id

    @property
    def home_team(self) -> str:
        return self.report.home_team

    @property
    def away_team(self) -> str:
        return self.report.away_team

    @property
    def kickoff_time(self) -> datetime:
        return self.report.commence_time

    @property
    def home_code(self) -> str:
        return short_team_code(self.home_team)

    @property
    def away_code(self) -> str:
        return short_team_code(self.away_team)

    @property
    def recommendation(self) -> Optional[str]:
        return self.report.recommendation

    def format_summary(self) -> str:
        rec = self.recommendation
        lines = [
            f"<b>{self.home_code} vs {self.away_code}</b>",
            f"{self.home_team} vs {self.away_team}",
            f"Kick-off: {self.kickoff_time:%Y-%m-%d %H:%M UTC}",
            "",
            "<b>Consensus</b>",
            self._consensus_line("home", self.home_code, self.report.average_home, rec),
            self._consensus_line("draw", "Draw", self.report.average_draw, rec),
            self._consensus_line("away", self.away_code, self.report.average_away, rec),
        ]

        lines.append("")
        if rec:
            confidence = (
                f"{self.report.recommendation_confidence * 100:.2f}%"
                if self.report.recommendation_confidence is not None
                else "N/A"
            )
            rec_label = self._outcome_label(rec)
            lines.extend([
                "<b>Recommendation</b>",
                f"🏁 <b>{rec_label}</b> (Δ {confidence})",
            ])
        else:
            lines.extend([
                "<b>Recommendation</b>",
                "No clear edge",
            ])
        return "\n".join(lines)

    def format_sources(self) -> str:
        rec = self.recommendation
        if not self.report.sources:
            return "No bookmaker data to display."
        lines = ["<b>Bookmakers</b>"]
        for src in sorted(self.report.sources, key=lambda s: s.source):
            parts = [
                self._source_component("home", self.home_code, src.home, rec),
                self._source_component("draw", "Draw", src.draw, rec),
                self._source_component("away", self.away_code, src.away, rec),
            ]
            lines.append(f"{src.source}: {' · '.join(parts)}")
        return "\n".join(lines)

    @staticmethod
    def _format_percent(value: Optional[float]) -> str:
        return f"{value * 100:.2f}%" if value is not None else "N/A"

    def _outcome_label(self, outcome: str) -> str:
        if outcome == "home":
            return self.home_code
        if outcome == "away":
            return self.away_code
        return "Draw"

    def _consensus_line(
        self,
        outcome: str,
        label: str,
        value: Optional[float],
        recommendation: Optional[str],
    ) -> str:
        prefix = "🏁 " if recommendation == outcome else "• "
        body = f"{label} {self._format_percent(value)}"
        if recommendation == outcome:
            return f"{prefix}<b>{body}</b>"
        return f"{prefix}{body}"

    def _source_component(
        self,
        outcome: str,
        label: str,
        value: Optional[float],
        recommendation: Optional[str],
    ) -> str:
        formatted = f"{label} {self._format_percent(value)}"
        if recommendation == outcome:
            return f"<b>{formatted}</b>"
        return formatted


@dataclass
class MatchDaySchedule:
    date: date
    matches: List[MatchOddsSummary] = field(default_factory=list)


@dataclass
class LeagueSchedule:
    league: str
    match_days: List[MatchDaySchedule]
    match_index: Dict[str, MatchOddsSummary]


class DataService:
    def __init__(
        self,
        odds_client: TheOddsAPIClient,
        *,
        cache_ttl_seconds: int = 6 * 3600,
    ) -> None:
        self.odds_client = odds_client
        self.aggregator = OddsAggregator(odds_client)
        self.cache_ttl = cache_ttl_seconds
        self.cache: Dict[str, tuple[float, LeagueSchedule]] = {}
        self._lock = asyncio.Lock()

    async def get_league_schedule(self, league: str) -> LeagueSchedule:
        async with self._lock:
            cached = self.cache.get(league)
            if cached and time.time() - cached[0] < self.cache_ttl:
                return cached[1]

        reports = await asyncio.to_thread(self.aggregator.fetch_league, league)
        schedule = self._build_schedule(league, reports)

        async with self._lock:
            self.cache[league] = (time.time(), schedule)

        return schedule

    async def get_match_summary(self, league: str, match_id: str) -> Optional[MatchOddsSummary]:
        schedule = await self.get_league_schedule(league)
        return schedule.match_index.get(str(match_id))

    def _build_schedule(
        self,
        league: str,
        reports: Sequence[MatchProbabilityReport],
    ) -> LeagueSchedule:
        match_days: Dict[date, MatchDaySchedule] = {}
        match_index: Dict[str, MatchOddsSummary] = {}

        for report in reports:
            if not (report.home_team and report.away_team and report.commence_time):
                continue

            summary = MatchOddsSummary(report=report)
            match_date = report.commence_time.date()

            day = match_days.setdefault(match_date, MatchDaySchedule(date=match_date))
            day.matches.append(summary)
            match_index[str(report.match_id)] = summary

        for day in match_days.values():
            day.matches.sort(key=lambda m: m.kickoff_time)

        ordered_days = sorted(match_days.values(), key=lambda md: md.date)

        return LeagueSchedule(
            league=league,
            match_days=ordered_days,
            match_index=match_index,
        )
