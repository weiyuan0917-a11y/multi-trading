"""美东交易时段、开盘禁交易、持仓时长等时间闸门。"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_zone(name: str) -> tzinfo:
    key = name or "America/New_York"
    try:
        return ZoneInfo(key)
    except ZoneInfoNotFoundError:
        # 缺少 IANA 数据时勿用固定 -5 冒充美东：会把 Asia/Shanghai 等名称错解，导致 RTH 统计全为 0。
        # requirements.txt 已包含 tzdata；若仍失败则退回 UTC（至少与服务器 K 线缓存的常见约定一致）。
        return timezone.utc


def to_ny(dt: datetime, tz_name: str) -> datetime:
    ny = get_zone("America/New_York")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=get_zone(tz_name))
    return dt.astimezone(ny)


def ny_date(dt: datetime, tz_name: str) -> date:
    return to_ny(dt, tz_name).date()


def rth_bounds(cfg, session_day: date) -> tuple[datetime, datetime]:
    z = get_zone("America/New_York")
    open_t = datetime.combine(session_day, time(cfg.rth_open_hour, cfg.rth_open_minute), z)
    close_t = datetime.combine(session_day, time(cfg.rth_close_hour, cfg.rth_close_minute), z)
    return open_t, close_t


def is_within_rth(dt: datetime, cfg, tz_name: str) -> bool:
    ny = to_ny(dt, tz_name)
    d = ny.date()
    open_t, close_t = rth_bounds(cfg, d)
    return open_t <= ny < close_t


def minutes_since_rth_open(dt: datetime, cfg, tz_name: str) -> float | None:
    ny = to_ny(dt, tz_name)
    d = ny.date()
    open_t, _ = rth_bounds(cfg, d)
    if ny < open_t:
        return None
    return (ny - open_t).total_seconds() / 60.0


def is_no_trade_opening_window(dt: datetime, cfg, tz_name: str) -> bool:
    m = minutes_since_rth_open(dt, cfg, tz_name)
    if m is None:
        return True
    return 0 <= m < float(cfg.no_trade_first_minutes)


def is_restricted_opening_period(dt: datetime, cfg, tz_name: str) -> bool:
    """策略描述中的开盘前几分钟内限制（可与 no_trade 重叠，用于 Regime）。"""
    m = minutes_since_rth_open(dt, cfg, tz_name)
    if m is None:
        return True
    return 0 <= m < float(cfg.restricted_opening_minutes)


def new_trades_cutoff_datetime(session_day: date, cfg: Any) -> datetime | None:
    """当日美东「新开仓截止」时刻；未启用则 None。"""
    if not bool(getattr(cfg, "no_new_trades_after_enabled", False)):
        return None
    z = get_zone("America/New_York")
    h = int(getattr(cfg, "no_new_trades_after_hour_et", 16))
    mi = int(getattr(cfg, "no_new_trades_after_minute_et", 0))
    h = min(23, max(0, h))
    mi = min(59, max(0, mi))
    return datetime.combine(session_day, time(h, mi), z)


def is_past_new_trade_cutoff(dt: datetime, cfg: Any, tz_name: str) -> bool:
    """当前 bar（换算到美东）是否已达/超过新开仓截止时间（仅用于空仓分支）。"""
    d = ny_date(dt, tz_name)
    cutoff = new_trades_cutoff_datetime(d, cfg)
    if cutoff is None:
        return False
    ny = to_ny(dt, tz_name)
    return ny >= cutoff


def option_expiry_datetime(session_day: date, cfg) -> datetime:
    z = get_zone("America/New_York")
    return datetime.combine(
        session_day,
        time(cfg.option_expiry_hour_et, cfg.option_expiry_minute_et),
        z,
    )


def minutes_between(start: datetime, end: datetime, tz_name: str) -> float:
    a = to_ny(start, tz_name)
    b = to_ny(end, tz_name)
    return (b - a).total_seconds() / 60.0


def parse_hhmm_et(hhmm: str) -> tuple[int, int] | None:
    """解析美东配置用时刻字符串，如 '09:35' -> (9, 35)。"""
    s = str(hhmm or "").strip()
    parts = s.split(":")
    if len(parts) != 2:
        return None
    try:
        h = int(parts[0].strip())
        m = int(parts[1].strip())
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except ValueError:
        pass
    return None


def ny_minutes_since_midnight(ny: datetime) -> int:
    return int(ny.hour) * 60 + int(ny.minute)


def is_within_et_hhmm_interval(ny: datetime, start_hhmm: str, end_hhmm: str) -> bool:
    """美东墙钟：start/end 均为当天同一日历日，闭区间 [start, end]（按 bar 的分钟对齐）。"""
    ps = parse_hhmm_et(start_hhmm)
    pe = parse_hhmm_et(end_hhmm)
    if not ps or not pe:
        return False
    t = ny_minutes_since_midnight(ny)
    a = ps[0] * 60 + ps[1]
    b = pe[0] * 60 + pe[1]
    return a <= t <= b


def is_at_or_after_et_hhmm(ny: datetime, hhmm: str) -> bool:
    """美东墙钟：当前时刻是否已达/超过 hhmm（按分钟）。"""
    p = parse_hhmm_et(hhmm)
    if not p:
        return False
    return ny_minutes_since_midnight(ny) >= p[0] * 60 + p[1]
