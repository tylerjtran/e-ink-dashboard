"""One-time (or rare re-run) generator for render/climate_normals_cache.json.

Not part of the regular 15-min pipeline -- run this by hand whenever you
want to (re)compute the historical daily-mean-temperature baseline used by
the Weather box's "X warmer/cooler than normal" line.

Pulls 30 years of daily mean temperature (1991-2020, the standard WMO
climate-normals reference period) from Open-Meteo's historical archive API
for the dashboard's own coordinates -- that API is gridded reanalysis
(ERA5), not real per-town weather stations, so it covers any coordinates
directly with no need for a "nearby town" proxy. Groups all ~10,958 days by
month-day across the 30 years and averages each of the 366 groups (Feb 29
included, based on however many leap years fall in the range -- 8 for
1991-2020).

The result barely changes over time (that's the point of a 30-year normal),
so the regular pipeline just does a local lookup against the committed
output file -- no network call needed for this metric on any normal run.

Usage:
    python generate_climate_normals.py
"""
import json
from collections import defaultdict
from pathlib import Path

import requests
import yaml

HERE = Path(__file__).parent
OUT_PATH = HERE / "climate_normals_cache.json"
START_YEAR = 1991
END_YEAR = 2020  # inclusive -- standard 30-year WMO reference period
REQUEST_TIMEOUT = 30


def main() -> None:
    settings = yaml.safe_load((HERE / "config" / "settings.yaml").read_text(encoding="utf-8"))
    loc = settings["location"]

    r = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": loc["lat"],
            "longitude": loc["lon"],
            "start_date": f"{START_YEAR}-01-01",
            "end_date": f"{END_YEAR}-12-31",
            "daily": "temperature_2m_mean",
            "temperature_unit": "fahrenheit",
            "timezone": loc["timezone"],
        },
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    daily = r.json()["daily"]

    by_month_day = defaultdict(list)
    for iso_date, temp in zip(daily["time"], daily["temperature_2m_mean"]):
        if temp is None:
            continue
        month_day = iso_date[5:]  # "1994-07-12" -> "07-12"
        by_month_day[month_day].append(temp)

    normals = {
        month_day: round(sum(temps) / len(temps), 1)
        for month_day, temps in sorted(by_month_day.items())
    }

    print(f"Computed {len(normals)} day-of-year normals from {START_YEAR}-{END_YEAR} "
          f"({len(daily['time'])} total days fetched).")
    if "02-29" in normals:
        print(f"  Feb 29 sample size: {len(by_month_day['02-29'])} (leap years only, as expected)")

    OUT_PATH.write_text(json.dumps(normals, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
