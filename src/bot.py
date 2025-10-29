"""
Telegram bot exposing football odds summaries.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .odds_api import load_client_from_env as load_odds_client
from .services import DataService, LeagueSchedule, MatchDaySchedule, MatchOddsSummary

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SUPPORTED_LEAGUES = {
    "epl": "English Premier League",
    "la_liga": "La Liga",
    "bundesliga": "Bundesliga",
}


class LeagueSelection(CallbackData, prefix="league"):
    league: str


class DaySelection(CallbackData, prefix="day"):
    league: str
    date: str  # ISO format


class MatchSelection(CallbackData, prefix="match"):
    league: str
    date: str
    match_id: str


class BackToDays(CallbackData, prefix="backdays"):
    league: str


class BackToMatches(CallbackData, prefix="backmatches"):
    league: str
    date: str


class BackToLeagues(CallbackData, prefix="backleagues"):
    action: str = "menu"


class TelegramBotApp:
    def __init__(self) -> None:
        self.bot_token = self._require_env("TELEGRAM_BOT_TOKEN")
        odds_client = load_odds_client()
        self.data_service = DataService(odds_client)
        self.bot = Bot(
            token=self.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dp = Dispatcher()
        self._register_handlers()

    @staticmethod
    def _require_env(env_var: str) -> str:
        value = os.getenv(env_var)
        if not value:
            raise RuntimeError(f"Environment variable '{env_var}' is required.")
        return value

    def _register_handlers(self) -> None:
        router = self.dp

        @router.message(CommandStart())
        async def handle_start(message: Message) -> None:
            await self._send_league_menu(message)

        @router.message(Command("help"))
        async def handle_help(message: Message) -> None:
            await self._send_league_menu(message)

        @router.message(Command("menu"))
        async def handle_menu(message: Message) -> None:
            await self._send_league_menu(message)

        @router.callback_query(LeagueSelection.filter())
        async def handle_league_callback(
            callback: CallbackQuery, callback_data: LeagueSelection
        ) -> None:
            await callback.answer()
            await self._send_league_overview(callback.message, callback_data.league, edit=True)

        @router.callback_query(DaySelection.filter())
        async def handle_day_callback(
            callback: CallbackQuery, callback_data: DaySelection
        ) -> None:
            await callback.answer()
            schedule = await self.data_service.get_league_schedule(callback_data.league)
            match_day = _get_match_day(schedule, callback_data.date)
            if not match_day:
                await callback.message.edit_text("No fixtures for that day.")
                return
            await self._show_match_day(callback.message, schedule.league, match_day)

        @router.callback_query(MatchSelection.filter())
        async def handle_match_callback(
            callback: CallbackQuery, callback_data: MatchSelection
        ) -> None:
            await callback.answer()
            summary = await self.data_service.get_match_summary(
                callback_data.league, callback_data.match_id
            )
            if not summary:
                await callback.message.edit_text("Fixture details not available.")
                return
            await self._show_match_detail(
                callback.message, callback_data.league, callback_data.date, summary
            )

        @router.callback_query(BackToDays.filter())
        async def handle_back_to_days(
            callback: CallbackQuery, callback_data: BackToDays
        ) -> None:
            await callback.answer()
            schedule = await self.data_service.get_league_schedule(callback_data.league)
            await self._show_league_days(callback.message, schedule)

        @router.callback_query(BackToMatches.filter())
        async def handle_back_to_matches(
            callback: CallbackQuery, callback_data: BackToMatches
        ) -> None:
            await callback.answer()
            schedule = await self.data_service.get_league_schedule(callback_data.league)
            match_day = _get_match_day(schedule, callback_data.date)
            if not match_day:
                await callback.message.edit_text("No fixtures for that day.")
                return
            await self._show_match_day(callback.message, schedule.league, match_day)

        @router.callback_query(BackToLeagues.filter())
        async def handle_back_to_leagues(callback: CallbackQuery) -> None:
            await callback.answer()
            await self._send_league_menu(callback.message, edit=True)

    async def run(self) -> None:
        await self.bot.set_my_commands(
            [
                BotCommand(command="epl", description="English Premier League"),
                BotCommand(command="laliga", description="La Liga"),
                BotCommand(command="bundesliga", description="Bundesliga"),
            ]
        )
        await self.dp.start_polling(self.bot)

    async def _send_league_menu(self, message: Message, *, edit: bool = False) -> None:
        text = "⚽️ Choose a league"
        keyboard = InlineKeyboardBuilder()
        for key, label in SUPPORTED_LEAGUES.items():
            keyboard.button(text=label, callback_data=LeagueSelection(league=key))
        keyboard.adjust(1)

        if edit:
            try:
                await message.edit_text(text, reply_markup=keyboard.as_markup())
                return
            except TelegramBadRequest:
                pass
        await message.answer(text, reply_markup=keyboard.as_markup())

    async def _send_league_overview(
        self,
        message: Message,
        league: str,
        *,
        edit: bool = False,
    ) -> None:
        try:
            schedule = await self.data_service.get_league_schedule(league)
        except Exception as exc:
            logger.exception("Failed to fetch schedule: %s", exc)
            await message.answer(f"Error fetching data: {exc}")
            return

        if not schedule.match_days:
            await message.answer("No upcoming fixtures in the next week.")
            return

        await self._show_league_days(message, schedule, edit=edit)

    async def _show_league_days(
        self,
        message: Message,
        schedule: LeagueSchedule,
        *,
        edit: bool = True,
    ) -> None:
        text, markup = self._build_league_days_view(schedule)
        if edit:
            try:
                await message.edit_text(text, reply_markup=markup)
                return
            except TelegramBadRequest as exc:
                logger.debug("Failed to edit message: %s. Sending new message.", exc)
        await message.answer(text, reply_markup=markup)

    async def _show_match_day(
        self,
        message: Message,
        league: str,
        match_day: MatchDaySchedule,
    ) -> None:
        if not match_day.matches:
            await message.edit_text("No matches scheduled for that day.")
            return
        keyboard = InlineKeyboardBuilder()
        league_name = SUPPORTED_LEAGUES.get(league, league.title())
        lines = [
            f"<b>{league_name}</b>",
            match_day.date.strftime("%A %d %b"),
            "",
            "Pick a match:",
        ]
        for summary in match_day.matches:
            kickoff = summary.kickoff_time.strftime("%H:%M")
            label = f"{kickoff} {summary.home_code} vs {summary.away_code}"
            keyboard.button(
                text=label,
                callback_data=MatchSelection(
                    league=league,
                    date=match_day.date.isoformat(),
                    match_id=str(summary.match_id),
                ),
            )
        keyboard.button(
            text="⬅️ Back to days",
            callback_data=BackToDays(league=league),
        )
        keyboard.button(
            text="🏟 Leagues",
            callback_data=BackToLeagues(),
        )
        keyboard.adjust(1)
        await message.edit_text(
            "\n".join(lines),
            reply_markup=keyboard.as_markup(),
        )

    async def _show_match_detail(
        self,
        message: Message,
        league: str,
        date_iso: str,
        summary: MatchOddsSummary,
    ) -> None:
        text = summary.format_summary() + "\n\n" + summary.format_sources()
        keyboard = InlineKeyboardBuilder()
        keyboard.button(
            text="⬅️ Back to matches",
            callback_data=BackToMatches(league=league, date=date_iso),
        )
        keyboard.button(
            text="🔄 Refresh",
            callback_data=MatchSelection(
                league=league,
                date=date_iso,
                match_id=str(summary.match_id),
            ),
        )
        keyboard.button(
            text="🏟 Leagues",
            callback_data=BackToLeagues(),
        )
        keyboard.adjust(1)
        await message.edit_text(
            text,
            reply_markup=keyboard.as_markup(),
        )

    def _build_league_days_view(
        self, schedule: LeagueSchedule
    ) -> tuple[str, InlineKeyboardMarkup]:
        keyboard = InlineKeyboardBuilder()
        for match_day in schedule.match_days:
            day_str = match_day.date.strftime("%A %d %b")
            keyboard.button(
                text=day_str,
                callback_data=DaySelection(
                    league=schedule.league,
                    date=match_day.date.isoformat(),
                ),
            )
        keyboard.button(text="🏟 Leagues", callback_data=BackToLeagues())
        keyboard.adjust(1)
        league_name = SUPPORTED_LEAGUES.get(schedule.league, schedule.league.title())
        text = f"<b>{league_name}</b>\nChoose a match day"
        return text, keyboard.as_markup()


def _get_match_day(schedule: LeagueSchedule, date_iso: str) -> Optional[MatchDaySchedule]:
    try:
        target_date = date.fromisoformat(date_iso)
    except ValueError:
        return None
    for match_day in schedule.match_days:
        if match_day.date == target_date:
            return match_day
    return None


async def main() -> None:
    app = TelegramBotApp()
    await app.run()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
