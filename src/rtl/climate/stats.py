"""Pure functions that turn an hourly temperature series into the climate_h3 statistics.

Arrays are shaped (..., hours) or (..., days, 24) so the same code works for one grid node, the whole grid
or every H3 cell. Kept free of I/O so tests can feed planted series and check the arithmetic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CAT_OFFSET_H = 2  # Central Africa Time = UTC+2 all year (Rwanda has no daylight saving)


def to_local_days(t_utc: np.ndarray, times_utc: pd.DatetimeIndex, first_day: str, last_day: str,
                  offset_h: int = CAT_OFFSET_H) -> tuple[np.ndarray, pd.DatetimeIndex]:
    """Re-index hourly data (..., hours) from UTC to local time and cut it into whole local days.

    Returns (..., n_days, 24) where [..., d, h] is local hour h of local day d, plus the local dates.
    Local time = UTC + offset, so local 00:00 on `first_day` is UTC 22:00 the day before (for +2).
    """
    local = times_utc.tz_convert(None) + pd.Timedelta(hours=offset_h)
    start, end = pd.Timestamp(first_day), pd.Timestamp(last_day) + pd.Timedelta(hours=23)
    keep = (local >= start) & (local <= end)
    kept = local[keep]
    expected = pd.date_range(start, end, freq="h")
    if len(kept) != len(expected) or not (kept == expected).all():
        raise ValueError("hourly series has gaps or does not cover the requested local days")
    days = pd.date_range(first_day, last_day, freq="D")
    out = t_utc[..., keep]
    return out.reshape(*out.shape[:-1], len(days), 24), days


def degree_days_per_year(daily_mean: np.ndarray, days: pd.DatetimeIndex, base_c: float) -> np.ndarray:
    """Cooling degree-days: sum over days of max(0, Tmean_day - base), summed per calendar year, then
    averaged over years. Tmean_day is the mean of the 24 hourly values (not (Tmax+Tmin)/2)."""
    excess = np.clip(daily_mean - base_c, 0, None)
    years = days.year.to_numpy()
    per_year = [excess[..., years == y].sum(axis=-1) for y in np.unique(years)]
    return np.mean(per_year, axis=0)


def summarise(t_days: np.ndarray, days: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    """All climate_h3 temperature statistics from a (..., n_days, 24) local-time array."""
    daily_mean = t_days.mean(axis=-1)
    daily_max = t_days.max(axis=-1)
    return {
        "t2m_mean_c": daily_mean.mean(axis=-1),
        "tmax_p95_c": np.percentile(daily_max, 95, axis=-1),
        "cdd18_per_year": degree_days_per_year(daily_mean, days, 18.0),
        "cdd24_per_year": degree_days_per_year(daily_mean, days, 24.0),
        "typical_day": t_days.mean(axis=-2),  # (..., 24): mean of each local hour over all days
    }


def to_kwh_m2_day(value: np.ndarray, units: str) -> np.ndarray:
    """NASA POWER irradiance units -> kWh/m2/day (RE community returns kWh already, AG returns MJ)."""
    u = units.replace(" ", "").lower()
    if u in {"kw-hr/m^2/day", "kwh/m^2/day"}:
        return value
    if u == "mj/m^2/day":
        return value / 3.6  # 1 kWh = 3.6 MJ
    raise ValueError(f"unknown irradiance unit {units!r}")
