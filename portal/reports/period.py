from datetime import datetime, time, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_date

PRESETS = ("today", "7d", "30d", "month", "custom")


def _preset_range(period, today):
    if period == "today":
        return today, today
    if period == "7d":
        return today - timedelta(days=6), today
    if period == "30d":
        return today - timedelta(days=29), today
    if period == "month":
        return today.replace(day=1), today
    return today - timedelta(days=29), today


def period_label(period, date_from, date_to):
    if period == "today":
        return "Today"
    if period == "7d":
        return "Last 7 days"
    if period == "30d":
        return "Last 30 days"
    if period == "month":
        return "This month"
    return f"{date_from.strftime('%d %b %Y')} – {date_to.strftime('%d %b %Y')}"


def parse_period_bounds(request, *, default_period="30d"):
    """Return period context for dashboard and report date filtering."""
    today = timezone.localdate()
    period = (request.GET.get("period") or default_period).lower()
    if period not in PRESETS:
        period = default_period

    raw_from = request.GET.get("date_from") or ""
    raw_to = request.GET.get("date_to") or ""

    if period == "custom":
        date_from = parse_date(raw_from) or today.replace(day=1)
        date_to = parse_date(raw_to) or today
    else:
        date_from, date_to = _preset_range(period, today)

    if date_from > date_to:
        date_from, date_to = date_to, date_from

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(date_from, time.min), tz)
    end_dt = timezone.make_aware(
        datetime.combine(date_to + timedelta(days=1), time.min), tz
    )

    return {
        "period": period,
        "date_from": date_from,
        "date_to": date_to,
        "date_from_iso": date_from.isoformat(),
        "date_to_iso": date_to.isoformat(),
        "start_dt": start_dt,
        "end_dt": end_dt,
        "period_label": period_label(period, date_from, date_to),
        "period_days": (date_to - date_from).days + 1,
    }
