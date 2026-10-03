"""Self-contained interactive Telegram Inline Calendar picker matching legacy DetailedTelegramCalendar behavior.

Generates drill-down inline keyboards for (Year -> Month -> Day) or directly Month -> Day
and processes callback queries without third-party library dependencies.
"""
import calendar
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple


LSTEP = {"y": "year", "m": "month", "d": "day"}
MONTH_NAMES = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
]
DAY_NAMES = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


class TelegramCalendar:
    """Builds and processes inline calendar keyboards.

    Callback data format:
      cbcal_{calendar_id}_{action}_{year}_{month}_{day}
      e.g. cbcal_0_d_2026_10_4 (selected day 4 of Oct 2026)
           cbcal_0_m_2026_10_0 (navigate to month 10)
           cbcal_0_y_2026_0_0 (navigate to year 2026)
    """

    PREFIX = "cbcal"

    def __init__(
        self,
        calendar_id: int = 0,
        min_date: Optional[date] = None,
        max_date: Optional[date] = None,
        locale: str = "en",
    ):
        self.calendar_id = calendar_id
        self.min_date = min_date or date.today()
        self.max_date = max_date or date(self.min_date.year + 2, 12, 31)

    def build(self, year: Optional[int] = None, month: Optional[int] = None) -> Tuple[Dict[str, Any], str]:
        """Builds the default initial calendar view (days of current/selected month)."""
        target_year = year or self.min_date.year
        target_month = month or self.min_date.month
        return self._build_days_view(target_year, target_month), "day"

    def process(self, callback_data: str) -> Tuple[Optional[date], Optional[Dict[str, Any]], str]:
        """Processes callback query data and returns (result_date, keyboard, current_step).

        - If date is selected, returns (date_obj, None, 'day')
        - If navigating, returns (None, inline_keyboard, step)
        """
        if not callback_data.startswith(f"{self.PREFIX}_"):
            return None, None, ""

        parts = callback_data.split("_")
        if len(parts) < 6:
            return None, None, ""

        _, cal_id, action, y_str, m_str, d_str = parts[:6]
        year = int(y_str)
        month = int(m_str)
        day = int(d_str)

        if action == "ignore":
            return None, None, "ignore"

        elif action == "day":
            # Action to switch to days view of a given year and month
            return None, self._build_days_view(year, month), "day"

        elif action == "month":
            # Action to switch to month view of a given year
            return None, self._build_months_view(year), "month"

        elif action == "year":
            # Action to switch to year view
            return None, self._build_years_view(year), "year"

        elif action == "set-day":
            # User clicked a specific day button!
            selected_date = date(year, month, day)
            return selected_date, None, "day"

        elif action == "set-month":
            # User selected a month -> show days of this month
            return None, self._build_days_view(year, month), "day"

        elif action == "set-year":
            # User selected a year -> show months of this year
            return None, self._build_months_view(year), "month"

        return None, None, ""

    def _build_days_view(self, year: int, month: int) -> Dict[str, Any]:
        """Generates days keyboard for given month & year."""
        keyboard: List[List[Dict[str, str]]] = []

        # Header: Clicking header switches to Month selection
        month_name = MONTH_NAMES[month]
        header_text = f"{month_name} {year}"
        keyboard.append([
            {"text": header_text, "callback_data": f"{self.PREFIX}_{self.calendar_id}_month_{year}_0_0"}
        ])

        # Weekday labels
        weekdays_row = [{"text": day_name, "callback_data": f"{self.PREFIX}_{self.calendar_id}_ignore_0_0_0"} for day_name in DAY_NAMES]
        keyboard.append(weekdays_row)

        # Days matrix
        month_matrix = calendar.monthcalendar(year, month)
        for week in month_matrix:
            row: List[Dict[str, str]] = []
            for d in week:
                if d == 0:
                    row.append({"text": " ", "callback_data": f"{self.PREFIX}_{self.calendar_id}_ignore_0_0_0"})
                else:
                    curr_date = date(year, month, d)
                    if curr_date < self.min_date or curr_date > self.max_date:
                        # Disabled day
                        row.append({"text": f"·{d}·", "callback_data": f"{self.PREFIX}_{self.calendar_id}_ignore_0_0_0"})
                    else:
                        row.append({"text": str(d), "callback_data": f"{self.PREFIX}_{self.calendar_id}_set-day_{year}_{month}_{d}"})
            keyboard.append(row)

        # Month Navigation (Prev / Next Month)
        prev_month = month - 1 if month > 1 else 12
        prev_year = year if month > 1 else year - 1
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1

        nav_row: List[Dict[str, str]] = []
        if date(prev_year, prev_month, calendar.monthrange(prev_year, prev_month)[1]) >= self.min_date:
            nav_row.append({"text": "<", "callback_data": f"{self.PREFIX}_{self.calendar_id}_day_{prev_year}_{prev_month}_0"})
        else:
            nav_row.append({"text": " ", "callback_data": f"{self.PREFIX}_{self.calendar_id}_ignore_0_0_0"})

        nav_row.append({"text": " ", "callback_data": f"{self.PREFIX}_{self.calendar_id}_ignore_0_0_0"})

        if date(next_year, next_month, 1) <= self.max_date:
            nav_row.append({"text": ">", "callback_data": f"{self.PREFIX}_{self.calendar_id}_day_{next_year}_{next_month}_0"})
        else:
            nav_row.append({"text": " ", "callback_data": f"{self.PREFIX}_{self.calendar_id}_ignore_0_0_0"})

        keyboard.append(nav_row)
        return {"inline_keyboard": keyboard}

    def _build_months_view(self, year: int) -> Dict[str, Any]:
        """Generates month selection keyboard for given year."""
        keyboard: List[List[Dict[str, str]]] = []

        # Header: clicking year switches to Years view
        keyboard.append([
            {"text": str(year), "callback_data": f"{self.PREFIX}_{self.calendar_id}_year_{year}_0_0"}
        ])

        # 4 rows of 3 months each
        for row_idx in range(4):
            row: List[Dict[str, str]] = []
            for col_idx in range(3):
                m = row_idx * 3 + col_idx + 1
                month_name = MONTH_NAMES[m]
                last_day = calendar.monthrange(year, m)[1]
                month_end = date(year, m, last_day)
                month_start = date(year, m, 1)
                if month_end < self.min_date or month_start > self.max_date:
                    row.append({"text": f"·{month_name}·", "callback_data": f"{self.PREFIX}_{self.calendar_id}_ignore_0_0_0"})
                else:
                    row.append({"text": month_name, "callback_data": f"{self.PREFIX}_{self.calendar_id}_set-month_{year}_{m}_0"})
            keyboard.append(row)

        return {"inline_keyboard": keyboard}

    def _build_years_view(self, center_year: int) -> Dict[str, Any]:
        """Generates year selection keyboard."""
        keyboard: List[List[Dict[str, str]]] = []
        min_year = self.min_date.year
        max_year = self.max_date.year

        years = list(range(min_year, max_year + 1))
        # Split years into rows of 3
        for i in range(0, len(years), 3):
            row = [
                {"text": str(y), "callback_data": f"{self.PREFIX}_{self.calendar_id}_set-year_{y}_0_0"}
                for y in years[i:i+3]
            ]
            keyboard.append(row)

        return {"inline_keyboard": keyboard}
