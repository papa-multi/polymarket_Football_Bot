"""
Command-line entry point for the betting odds aggregator demo.

Example usage:

    export THE_ODDS_API_KEY="..."
    python -m src.main --leagues epl la_liga
"""

from __future__ import annotations

import argparse
import sys
from typing import Iterable, List, Optional, Sequence

from tabulate import tabulate

from .odds_api import (
    LEAGUE_TO_SPORT_KEY,
    MatchProbabilityReport,
    OddsAggregator,
    load_client_from_env,
)
from .polymarket import PolymarketClient, match_reports_to_polymarket


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch and aggregate football odds from The Odds API."
    )
    parser.add_argument(
        "--leagues",
        nargs="+",
        metavar="LEAGUE",
        help="League keys to fetch (default: all supported leagues).",
    )
    parser.add_argument(
        "--regions",
        default="uk,eu",
        help="Comma-separated regions to request from The Odds API (default: uk,eu).",
    )
    parser.add_argument(
        "--bookmakers",
        nargs="+",
        help="Optional list of bookmaker keys to restrict responses.",
    )
    parser.add_argument(
        "--polymarket-only",
        action="store_true",
        help="Only display matches that exist as active markets on Polymarket.",
    )
    parser.add_argument(
        "--polymarket-endpoint",
        default="https://polymarket.com/api/markets",
        help="Override Polymarket markets endpoint.",
    )
    return parser.parse_args(argv)


def _format_percent(value: Optional[float]) -> str:
    return f"{value * 100:.2f}%" if value is not None else "N/A"


def _render_sources(report: MatchProbabilityReport) -> str:
    rows = []
    for source in sorted(report.sources, key=lambda s: s.source):
        rows.append(
            [
                source.source,
                _format_percent(source.home),
                _format_percent(source.draw),
                _format_percent(source.away),
            ]
        )
    return tabulate(
        rows,
        headers=["Source", "Home", "Draw", "Away"],
        tablefmt="plain",
    )


def _render_summary(report: MatchProbabilityReport) -> str:
    rows = [
        ["Home", _format_percent(report.average_home)],
        ["Draw", _format_percent(report.average_draw)],
        ["Away", _format_percent(report.average_away)],
    ]
    if report.recommendation:
        confidence = (
            f"{report.recommendation_confidence * 100:.2f}%"
            if report.recommendation_confidence is not None
            else "N/A"
        )
        rows.append(
            [
                "Recommendation",
                f"{report.recommendation.upper()} (Δ {confidence})",
            ]
        )
    return tabulate(rows, tablefmt="plain")


def run(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    league_keys: Iterable[str]
    if args.leagues:
        league_keys = [league.lower() for league in args.leagues]
        unknown = [league for league in league_keys if league not in LEAGUE_TO_SPORT_KEY]
        if unknown:
            print(
                f"Unsupported league key(s): {', '.join(unknown)}. "
                f"Supported: {', '.join(sorted(LEAGUE_TO_SPORT_KEY))}",
                file=sys.stderr,
            )
            return 2
    else:
        league_keys = sorted(LEAGUE_TO_SPORT_KEY)

    client = load_client_from_env()
    aggregator = OddsAggregator(
        client,
        regions=args.regions,
        bookmakers=args.bookmakers,
    )
    reports: List[MatchProbabilityReport] = aggregator.fetch_many(list(league_keys))

    polymarket_mapping = {}
    if reports:
        try:
            poly_client = PolymarketClient(base_url=args.polymarket_endpoint)
            markets = poly_client.get_active_markets()
            polymarket_mapping = match_reports_to_polymarket(reports, markets)
        except Exception as exc:
            print(
                f"Warning: failed to fetch Polymarket markets ({exc}).",
                file=sys.stderr,
            )

    if args.polymarket_only:
        reports = [report for report in reports if report.match_id in polymarket_mapping]

    if not reports:
        print("No upcoming matches returned by The Odds API.")
        return 0

    for report in reports:
        on_polymarket = report.match_id in polymarket_mapping
        header = (
            f"{report.league.upper()} | "
            f"{report.home_team} vs {report.away_team} | "
            f"{report.commence_time:%Y-%m-%d %H:%M UTC}"
        )
        if on_polymarket:
            header = "[Polymarket] " + header
        print("=" * len(header))
        print(header)
        print("=" * len(header))
        print("Per-source probabilities:")
        print(_render_sources(report))
        print()
        print("Aggregated summary:")
        print(_render_summary(report))
        print("\n")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
