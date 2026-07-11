"""Pull all dashboard data from external sources and write data.json.

Run with the same Python env used for render.py:
    python fetch_data.py [--out data.json]

Requires these environment variables (see ../docs/SETUP.md):
    AMBIENT_WEATHER_API_KEY
    AMBIENT_WEATHER_APPLICATION_KEY
    AMBIENT_WEATHER_MAC
    GOOGLE_CALENDAR_ICAL_URL

For local testing, put these in a gitignored render/.env file (KEY=value per
line) instead of exporting them by hand -- they'll be loaded automatically.
"""
import argparse
import json
import os
import re
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from astral import moon
from dotenv import load_dotenv
from icalendar import Calendar

HERE = Path(__file__).parent
CONFIG = HERE / "config"
load_dotenv(HERE / ".env")
REQUEST_TIMEOUT = 10


def fmt_date_long(dt):
    """'Friday, July 10' -- avoids %-d, which Windows' strftime doesn't support."""
    return f"{dt:%A, %B} {dt.day}"


def fmt_time12(dt):
    """'6:05pm'"""
    hour12 = dt.hour % 12 or 12
    return f"{hour12}:{dt.minute:02d}{dt.strftime('%p').lower()}"


def fmt_hour12(dt):
    """'6pm'"""
    hour12 = dt.hour % 12 or 12
    return f"{hour12}{dt.strftime('%p').lower()}"


def fmt_month_day(d):
    """'July 14'"""
    return f"{d:%B} {d.day}"

WMO_CODE_TEXT = {
    0: "Clear", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Foggy", 48: "Foggy",
    51: "Light Drizzle", 53: "Drizzle", 55: "Heavy Drizzle",
    61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
    66: "Freezing Rain", 67: "Freezing Rain",
    71: "Light Snow", 73: "Snow", 75: "Heavy Snow", 77: "Snow Grains",
    80: "Rain Showers", 81: "Rain Showers", 82: "Heavy Rain Showers",
    85: "Snow Showers", 86: "Snow Showers",
    95: "Thunderstorms", 96: "Thunderstorms", 99: "Thunderstorms",
}

MOON_PHASE_NAMES = [
    (1, "New moon"), (6.5, "Waxing crescent"), (8.5, "First quarter"),
    (13.5, "Waxing gibbous"), (15.5, "Full moon"), (20.5, "Waning gibbous"),
    (22.5, "Last quarter"), (27.5, "Waning crescent"), (29, "New moon"),
]


def load_yaml(name):
    return yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))


def now_local(tz_name):
    return datetime.now(ZoneInfo(tz_name))


# ---------------------------------------------------------------- weather --

def fetch_weather(settings):
    # Open-Meteo and Ambient Weather are independent sources -- if one is
    # down, the other's data (if any) should still make it onto the
    # dashboard rather than taking every weather field (and every other box,
    # since main() only writes data.json after every fetch succeeds) down
    # with it.
    om = _fetch_open_meteo_weather(settings)
    aw = _fetch_ambient_weather_safe(settings)

    outdoor_temp_f = round(aw["tempf"]) if aw else (om["outdoor_temp_f"] if om else None)
    indoor_temp_f = round(aw["tempinf"]) if aw else None

    high_f = om["high_f"] if om else None
    low_f = om["low_f"] if om else None
    condition = om["condition"] if om else "Unknown"
    chance_rain_pct = om["chance_rain_pct"] if om else None
    weather_code = om["weather_code"] if om else None
    tonight_cloud_cover_pct = om["tonight_cloud_cover_pct"] if om else 50  # neutral guess

    try:
        normal_diff_str = compute_normal_diff(settings, high_f) if high_f is not None else ""
    except Exception as e:
        print(f"[warn] Normal-diff computation failed: {e}")
        normal_diff_str = ""
    philly_diff_str = compute_philly_diff(settings, outdoor_temp_f) if outdoor_temp_f is not None else ""

    return {
        "temp_f": _dash_if_none(outdoor_temp_f),
        "condition": condition,
        "chance_rain_pct": _dash_if_none(chance_rain_pct),
        "high_f": _dash_if_none(high_f),
        "low_f": _dash_if_none(low_f),
        "normal_diff_str": normal_diff_str,
        "philly_diff_str": philly_diff_str,
        "indoor_temp_f": _dash_if_none(indoor_temp_f if indoor_temp_f is not None else outdoor_temp_f),
        "burn_ban_str": "Burn ban in effect" if settings["burn_ban_active"] else "No burn ban",
        "icon_name": weather_icon_name(weather_code) if weather_code is not None else "device_thermostat",
        "_tonight_cloud_cover_pct": tonight_cloud_cover_pct,  # used by skygazing, not displayed directly
    }


def _dash_if_none(value):
    return value if value is not None else "—"


def _fetch_open_meteo_weather(settings):
    try:
        loc = settings["location"]
        om = settings["open_meteo"]
        params = {
            "latitude": loc["lat"],
            "longitude": loc["lon"],
            "current": "temperature_2m,weather_code",
            "hourly": "cloud_cover",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
            "temperature_unit": "fahrenheit",
            "forecast_days": 1,
            "timezone": loc["timezone"],
        }
        r = requests.get(om["base_url"], params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        current = d["current"]
        daily = d["daily"]
        weather_code = current.get("weather_code", daily["weather_code"][0])

        return {
            "outdoor_temp_f": round(current["temperature_2m"]),
            "high_f": round(daily["temperature_2m_max"][0]),
            "low_f": round(daily["temperature_2m_min"][0]),
            "condition": WMO_CODE_TEXT.get(weather_code, "Unknown"),
            "chance_rain_pct": daily["precipitation_probability_max"][0],
            "weather_code": weather_code,
            "tonight_cloud_cover_pct": compute_tonight_cloud_cover(d["hourly"]),
        }
    except Exception as e:
        print(f"[warn] Open-Meteo weather fetch failed: {e}")
        return None


def _fetch_ambient_weather_safe(settings):
    try:
        return fetch_ambient_weather(settings)
    except Exception as e:
        print(f"[warn] Ambient Weather fetch failed: {e}")
        return None


def compute_philly_diff(settings, outdoor_temp_f):
    try:
        philly = settings["philly"]
        r = requests.get(
            settings["open_meteo"]["base_url"],
            params={
                "latitude": philly["lat"],
                "longitude": philly["lon"],
                "current": "temperature_2m",
                "temperature_unit": "fahrenheit",
                "forecast_days": 1,
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        philly_temp_f = r.json()["current"]["temperature_2m"]
        diff = round(outdoor_temp_f - philly_temp_f)
        if diff > 0:
            return f"{diff}° warmer than Philly"
        if diff < 0:
            return f"{-diff}° cooler than Philly"
        return "Same as Philly"
    except Exception as e:
        print(f"[warn] Philly comparison fetch failed: {e}")
        return ""


def compute_tonight_cloud_cover(hourly):
    """Average forecast cloud cover for 9-11pm today -- skygazing conditions
    describe tonight's sky, not whatever it looks like right now."""
    tonight_hours = ("T21:00", "T22:00", "T23:00")
    values = [
        cover
        for t, cover in zip(hourly["time"], hourly["cloud_cover"])
        if t.endswith(tonight_hours)
    ]
    if not values:
        return hourly["cloud_cover"][-1] if hourly["cloud_cover"] else 0
    return sum(values) / len(values)


def weather_icon_name(weather_code):
    if weather_code == 0:
        return "clear_day"
    if weather_code in (1, 2):
        return "partly_cloudy_day"
    if weather_code == 3:
        return "cloud"
    if weather_code in (45, 48):
        return "foggy"
    if weather_code in (51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "rainy"
    if weather_code in (66, 67, 71, 73, 75, 77, 85, 86):
        return "weather_snowy"
    if weather_code in (95, 96, 99):
        return "thunderstorm"
    return "device_thermostat"


def fetch_ambient_weather(settings):
    api_key = os.environ.get("AMBIENT_WEATHER_API_KEY")
    app_key = os.environ.get("AMBIENT_WEATHER_APPLICATION_KEY")
    mac = os.environ.get("AMBIENT_WEATHER_MAC")
    if not (api_key and app_key and mac):
        return None
    base = settings["ambient_weather"]["base_url"]
    r = requests.get(
        f"{base}/devices/{mac}",
        params={"apiKey": api_key, "applicationKey": app_key, "limit": 1},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    return data[0]


def compute_normal_diff(settings, high_f):
    month_key = date.today().strftime("%b").lower()
    normal_high = settings["climate_normals"][month_key]["high"]
    diff = high_f - normal_high
    if diff >= 2:
        return f"{diff}° warmer than normal"
    if diff <= -2:
        return f"{-diff}° cooler than normal"
    return "Near normal temperature"


# -------------------------------------------------------------- skygazing --

def fetch_skygazing(settings, cloud_cover_pct):
    today = date.today()
    phase_value = moon.phase(today)  # 0-27.99, 0/14 = new/full
    phase_name = moon_phase_name(phase_value)
    illumination_pct = moon_illumination_pct(phase_value)

    conditions_str = f"{rate_sky_conditions(cloud_cover_pct, illumination_pct)} conditions tonight."
    moon_phase_str = f"Moon: {phase_name}."

    event_str = next_meteor_shower_str(today)

    return {
        "conditions_str": conditions_str,
        "moon_phase_str": moon_phase_str,
        "event_str": event_str,
    }


def moon_phase_name(phase_value):
    for threshold, name in MOON_PHASE_NAMES:
        if phase_value <= threshold:
            return name
    return "New moon"


def moon_illumination_pct(phase_value):
    # phase_value: 0 = new, 14 = full, 28 = new again. Illumination follows
    # (1 - cos(2*pi*phase/28)) / 2.
    import math
    return round((1 - math.cos(2 * math.pi * phase_value / 28)) / 2 * 100)


def rate_sky_conditions(cloud_cover_pct, illumination_pct):
    # Lower cloud cover and lower moon illumination = better stargazing.
    score = cloud_cover_pct * 0.7 + illumination_pct * 0.3
    if score < 20:
        return "Excellent"
    if score < 45:
        return "Good"
    if score < 70:
        return "OK"
    return "Poor"


def next_meteor_shower_str(today, lookahead_days=14):
    showers = load_yaml("meteor_showers.yaml")
    year = today.year
    best = None
    for shower in showers:
        for y in (year, year + 1):
            try:
                peak = date(y, shower["peak_month"], shower["peak_day"])
            except ValueError:
                continue
            days_out = (peak - today).days
            if 0 <= days_out <= lookahead_days:
                if best is None or days_out < best[0]:
                    best = (days_out, shower["name"])
    if not best:
        return None
    days_out, name = best
    if days_out == 0:
        return f"{name} peaks tonight."
    if days_out == 1:
        return f"{name} peaks tomorrow."
    return f"{name} peaks in {days_out} days."


# --------------------------------------------------------- river/reservoir --

def fetch_river_reservoir(settings):
    river_temp_f, river_normal_diff_str = fetch_usgs_river_temp(settings)
    reservoir_pct_full, reservoir_note = fetch_nyc_reservoir(settings)

    return {
        "river_name": "East Branch of the Delaware",
        "river_temp_f": river_temp_f if river_temp_f is not None else "—",
        "river_normal_diff_str": river_normal_diff_str,
        "reservoir_name": "Pepacton Reservoir",
        "reservoir_pct_full": reservoir_pct_full if reservoir_pct_full is not None else "—",
        "reservoir_note": reservoir_note,
    }


def fetch_usgs_river_temp(settings):
    usgs = settings["usgs"]
    try:
        r = requests.get(
            "https://waterservices.usgs.gov/nwis/iv/",
            params={
                "format": "json",
                "sites": usgs["site_id"],
                "parameterCd": usgs["temperature_param_cd"],
                "period": "P1D",
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        series = r.json()["value"]["timeSeries"]
        values = series[0]["values"][0]["value"] if series else []
        if not values:
            return None, ""
        temp_c = float(values[-1]["value"])
        temp_f = round(temp_c * 9 / 5 + 32)
    except Exception as e:
        print(f"[warn] USGS river temp fetch failed: {e}")
        return None, ""

    normal_diff_str = ""
    try:
        median_c = fetch_usgs_daily_median(settings)
        if median_c is not None:
            diff = round(temp_f - (median_c * 9 / 5 + 32))
            if diff > 0:
                normal_diff_str = f"{diff}° warmer than normal"
            elif diff < 0:
                normal_diff_str = f"{-diff}° colder than normal"
            else:
                normal_diff_str = "Near normal temperature"
    except Exception as e:
        print(f"[warn] USGS median-temperature fetch failed: {e}")

    return temp_f, normal_diff_str


def fetch_usgs_daily_median(settings):
    """Median water temp (deg C) for today's day-of-year, from USGS's
    long-term daily statistics service (decades of history, not a forecast)."""
    usgs = settings["usgs"]
    r = requests.get(
        "https://waterservices.usgs.gov/nwis/stat/",
        params={
            "format": "rdb",
            "sites": usgs["site_id"],
            "statReportType": "daily",
            "statType": "median",
            "parameterCd": usgs["temperature_param_cd"],
        },
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    lines = [line for line in r.text.splitlines() if line and not line.startswith("#")]
    if len(lines) < 3:
        return None
    header = lines[0].split("\t")
    idx_month, idx_day, idx_p50 = header.index("month_nu"), header.index("day_nu"), header.index("p50_va")
    today = date.today()
    for row in lines[2:]:  # lines[1] is the rdb format-spec row (5s, 15s, ...)
        cols = row.split("\t")
        if int(cols[idx_month]) == today.month and int(cols[idx_day]) == today.day:
            return float(cols[idx_p50]) if cols[idx_p50] else None
    return None


def fetch_nyc_reservoir(settings):
    cfg = settings["nyc_reservoir"]
    try:
        # NYC DEP's own reservoir-levels page (not the Socrata "Current
        # Reservoir Levels" API -- that dataset's field names for the
        # Cannonsville/Pepacton block don't match their contents, and its
        # data lags by months. This page is server-rendered and current;
        # confirmed by hand against https://www.nyc.gov/site/dep/water/reservoir-levels.page.
        # Needs a browser-like User-Agent or nyc.gov returns 403.
        r = requests.get(
            cfg["dep_page_url"],
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        m = re.search(
            r'map-levels-reservoir pepacton">\s*<p><strong>[^<]*</strong><br />'
            r"Available Capacity: ([\d.]+) BG<br />% of Usable Storage: ([\d.]+)",
            r.text,
        )
        if not m:
            raise ValueError("Pepacton block not found in DEP page -- page layout may have changed")
        pct_full = round(float(m.group(2)))

        month_key = date.today().strftime("%b").lower()
        normal_pct = cfg["normal_pct_by_month"][month_key]
        if pct_full < normal_pct - 3:
            note = "Lower than normal"
        elif pct_full > normal_pct + 3:
            note = "Higher than normal"
        else:
            note = "Near normal"
        return pct_full, note
    except Exception as e:
        print(f"[warn] NYC reservoir fetch failed: {e}")
        return None, ""


# -------------------------------------------------------------- plant watch --

def fetch_plant_watch(settings):
    slug = settings["inaturalist"]["project_slug"]
    base = "https://api.inaturalist.org/v1/observations/species_counts"
    try:
        current = _inat_species_ids(base, slug)
        first_of_month = date.today().replace(day=1)
        day_before = first_of_month - timedelta(days=1)
        # observed_d2 (when the sighting happened), not created_d2 (when it
        # was added/IDed in iNaturalist) -- "new this month" tracks freshly
        # sighted species.
        before_this_month = _inat_species_ids(base, slug, observed_d2=day_before.isoformat())
        native = _inat_species_ids(base, slug, extra={"native": "true"})

        return {
            "species_count": len(current),
            "native_count": len(native),
            "new_this_month": len(current - before_this_month),
        }
    except Exception as e:
        print(f"[warn] iNaturalist fetch failed: {e}")
        return {"species_count": "—", "native_count": "—", "new_this_month": "—"}


def _inat_species_ids(base_url, project_slug, observed_d2=None, extra=None):
    # iconic_taxa=Plantae: the project also has non-plant (e.g. animal)
    # observations that shouldn't count toward "species identified".
    params = {"project_id": project_slug, "iconic_taxa": "Plantae", "per_page": 200}
    if observed_d2:
        params["observed_d2"] = observed_d2
    if extra:
        params.update(extra)
    ids = set()
    page = 1
    while True:
        params["page"] = page
        r = requests.get(base_url, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        d = r.json()
        for result in d["results"]:
            # Only count species-level IDs -- genus/family-level entries
            # (e.g. "Rubus" with no species determined) aren't a species.
            if result["taxon"]["rank"] == "species":
                ids.add(result["taxon"]["id"])
        if len(d["results"]) < params["per_page"] or page * params["per_page"] >= d["total_results"]:
            break
        page += 1
    return ids


# ----------------------------------------------------------- business watch --

def fetch_business_watch(now):
    hours = load_yaml("business_hours.yaml")
    day_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
    now_minutes = now.hour * 60 + now.minute

    result = []
    for name, week in hours.items():
        ranges = week.get(day_key, "closed")
        is_open = False
        if ranges != "closed":
            for r in ranges:
                start_str, end_str = r.split("-")
                start_min = _hhmm_to_minutes(start_str)
                end_min = _hhmm_to_minutes(end_str)
                if start_min <= now_minutes < end_min:
                    is_open = True
                    break
        result.append({"name": name, "is_open": is_open})
    return result


def _hhmm_to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# --------------------------------------------------------------- birthdays --

UPCOMING_BIRTHDAYS_COUNT = 3


def fetch_birthdays(today):
    ical_url = os.environ.get("GOOGLE_CALENDAR_ICAL_URL")
    if not ical_url:
        return {"today": [], "upcoming": []}
    try:
        r = requests.get(ical_url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        cal = Calendar.from_ical(r.content)

        people = []  # (name, month, day), one per calendar event
        for component in cal.walk("VEVENT"):
            dtstart = component.get("dtstart")
            if not dtstart:
                continue
            d = dtstart.dt
            if hasattr(d, "date"):
                d = d.date()
            people.append((str(component.get("summary")), d.month, d.day))

        today_names = [name for name, m, d in people if (m, d) == (today.month, today.day)]

        upcoming = []
        for name, m, d in people:
            if (m, d) == (today.month, today.day):
                continue
            next_date = _next_occurrence(today, m, d)
            if next_date is not None:
                upcoming.append((next_date, name))
        upcoming.sort(key=lambda pair: pair[0])
        upcoming_list = [
            {"name": name, "date_str": fmt_month_day(d)} for d, name in upcoming[:UPCOMING_BIRTHDAYS_COUNT]
        ]

        return {"today": today_names, "upcoming": upcoming_list}
    except Exception as e:
        print(f"[warn] Birthday calendar fetch failed: {e}")
        return {"today": [], "upcoming": []}


def _next_occurrence(today, month, day):
    """Next date (this year or next) that falls on the given month/day.

    Returns None for Feb 29 in a run-up to a non-leap year, rather than
    guessing which nearby date to show instead.
    """
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today:
            return candidate
    return None


# --------------------------------------------------------------- game watch --

def fetch_game_watch(settings, now):
    tz_name = settings["location"]["timezone"]
    return {
        "phillies": {"name": settings["mlb"]["team_short_name"], "status": fetch_mlb_game(settings, now)},
        "eagles": {
            "name": settings["nfl"]["team_name"],
            "status": fetch_espn_game("football", "nfl", settings["nfl"]["team_abbr"], now, tz_name),
        },
        "sixers": {
            "name": settings["nba"]["team_name"],
            "status": fetch_espn_game("basketball", "nba", settings["nba"]["team_abbr"], now, tz_name),
        },
        "flyers": {
            "name": settings["nhl"]["team_name"],
            "status": fetch_espn_game(
                "hockey", "nhl", settings["nhl"]["team_abbr"], now, tz_name, period_label="period"
            ),
        },
    }


def fmt_relative_game_date(game_date, today):
    if game_date == today:
        return "today"
    if game_date == today + timedelta(days=1):
        return "tomorrow"
    return f"{game_date:%B} {game_date.day}"


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def fetch_mlb_game(settings, now):
    team_id = settings["mlb"]["team_id"]
    tz_name = settings["location"]["timezone"]
    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={
                "sportId": 1,
                "teamId": team_id,
                "startDate": now.strftime("%Y-%m-%d"),
                "endDate": (now + timedelta(days=30)).strftime("%Y-%m-%d"),
            },
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        games = [g for d in r.json().get("dates", []) for g in d["games"]]
    except Exception as e:
        print(f"[warn] MLB schedule fetch failed: {e}")
        return None

    for game in games:
        if game["status"]["abstractGameState"] == "Live":
            return _mlb_live_status(game, _mlb_opponent_name(game, team_id))

    for game in games:
        if game["status"]["abstractGameState"] != "Preview":
            continue
        opponent = _mlb_opponent_name(game, team_id)
        game_dt = datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00")).astimezone(ZoneInfo(tz_name))
        return f"vs {opponent} {fmt_relative_game_date(game_dt.date(), now.date())} at {fmt_time12(game_dt)}"

    return None


def _mlb_opponent_name(game, team_id):
    teams = game["teams"]
    other = teams["away"] if teams["home"]["team"]["id"] == team_id else teams["home"]
    return other["team"]["name"].split()[-1]  # e.g. "Atlanta Braves" -> "Braves"


def _mlb_live_status(game, opponent):
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1.1/game/{game['gamePk']}/feed/live",
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        linescore = r.json()["liveData"]["linescore"]
        inning_state = linescore["inningState"].lower()  # "top", "bottom", "middle", "end"
        return f"vs {opponent} in progress, {inning_state} of {_ordinal(linescore['currentInning'])}"
    except Exception as e:
        print(f"[warn] MLB live feed fetch failed: {e}")
        return f"vs {opponent} in progress"


def fetch_espn_game(sport_path, league_path, team_abbr, now, tz_name, period_label="quarter"):
    try:
        r = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/{sport_path}/{league_path}/teams/{team_abbr}/schedule",
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        events = r.json().get("events", [])
    except Exception as e:
        print(f"[warn] ESPN {league_path} schedule fetch failed: {e}")
        return None

    today = now.date()
    upcoming = []
    for event in events:
        comp = event["competitions"][0]
        status = comp["status"]
        state = status["type"]["state"]  # "pre", "in", "post"
        opponent = _espn_opponent_name(comp, team_abbr)

        if state == "in":
            period = status.get("period")
            if period:
                return f"vs {opponent} in progress, {_ordinal(period)} {period_label}"
            return f"vs {opponent} in progress"

        if state == "pre":
            event_dt = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(ZoneInfo(tz_name))
            if event_dt.date() >= today:
                upcoming.append((event_dt, opponent))

    if not upcoming:
        return None
    upcoming.sort(key=lambda pair: pair[0])
    event_dt, opponent = upcoming[0]
    return f"vs {opponent} {fmt_relative_game_date(event_dt.date(), today)} at {fmt_time12(event_dt)}"


def _espn_opponent_name(comp, team_abbr):
    for competitor in comp["competitors"]:
        team = competitor["team"]
        if team["abbreviation"].lower() != team_abbr.lower():
            return team.get("nickname") or team.get("shortDisplayName") or team.get("displayName", "TBD")
    return "TBD"


# --------------------------------------------------------------- pie watch --

PIE_CACHE_PATH = HERE / "pie_cache.json"


def in_pie_blackout(now):
    """Sun 2pm through Tue 9am: between weekend pie runs, nothing new to show."""
    weekday = now.weekday()  # Mon=0 ... Sun=6
    if weekday == 6 and now.hour >= 14:  # Sunday, 2pm or later
        return True
    if weekday == 0:  # Monday, all day
        return True
    if weekday == 1 and now.hour < 9:  # Tuesday, before 9am
        return True
    return False


def current_pie_week_start(now):
    """The most recent Tuesday 9am at or before `now`."""
    days_since_tuesday = (now.weekday() - 1) % 7  # Mon=0 -> Tue=1
    candidate = (now - timedelta(days=days_since_tuesday)).replace(hour=9, minute=0, second=0, microsecond=0)
    if candidate > now:
        candidate -= timedelta(days=7)
    return candidate


def fetch_pie_watch(now):
    if in_pie_blackout(now):
        return {"message": "Stay tuned for next weekend's pie menu.", "pies": []}

    week_start = current_pie_week_start(now)
    cache = _load_pie_cache()
    if cache and datetime.fromisoformat(cache["scraped_at"]) >= week_start:
        return {"message": None, "pies": cache["pies"]}

    try:
        from scrape_pie import scrape_pies  # lazy: only needs Playwright when actually scraping
        pies = scrape_pies()
    except Exception as e:
        print(f"[warn] Pie scrape failed: {e}")
        pies = []

    if pies:
        _save_pie_cache(pies, now)
        return {"message": None, "pies": pies}

    if cache:
        # This week's scrape failed, but a (stale-ish) previous list beats nothing.
        return {"message": None, "pies": cache["pies"]}

    return {"message": "Check back soon — pie list coming shortly", "pies": []}


def _load_pie_cache():
    if not PIE_CACHE_PATH.exists():
        return None
    try:
        return json.loads(PIE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_pie_cache(pies, now):
    PIE_CACHE_PATH.write_text(
        json.dumps({"scraped_at": now.isoformat(), "pies": pies}, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------- electric --

def get_electric_note(settings, now):
    cfg = settings["electric"]
    day_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
    if day_key not in cfg["peak_days"]:
        return "Off-peak electric"
    now_minutes = now.hour * 60 + now.minute
    start_min = _hhmm_to_minutes(cfg["peak_start"])
    end_min = _hhmm_to_minutes(cfg["peak_end"])
    if start_min <= now_minutes < end_min:
        end_label = fmt_hour12(datetime.strptime(cfg["peak_end"], "%H:%M"))
        return f"On-peak electric until {end_label}"
    return "Off-peak electric"


# --------------------------------------------------------------------- main --

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "data.json"))
    args = parser.parse_args()

    settings = load_yaml("settings.yaml")
    tz_name = settings["location"]["timezone"]
    now = now_local(tz_name)
    today = now.date()

    weather = fetch_weather(settings)
    skygazing = fetch_skygazing(settings, weather.pop("_tonight_cloud_cover_pct"))
    river_reservoir = fetch_river_reservoir(settings)
    plant_watch = fetch_plant_watch(settings)
    business_watch = fetch_business_watch(now)
    birthdays = fetch_birthdays(today)
    game_watch = fetch_game_watch(settings, now)
    pie_watch = fetch_pie_watch(now)
    electric_note = get_electric_note(settings, now)

    # Grab the display timestamp last, so it's as close as possible to the
    # moment this script finishes, per the original business rule.
    display_now = now_local(tz_name)
    date_str = fmt_date_long(display_now)
    updated_time_str = fmt_time12(display_now)

    data = {
        "date_str": date_str,
        "updated_time_str": updated_time_str,
        "electric_note": electric_note,
        "weather": weather,
        "skygazing": skygazing,
        "river_reservoir": river_reservoir,
        "plant_watch": plant_watch,
        "business_watch": business_watch,
        "birthdays": birthdays,
        "game_watch": game_watch,
        "pie_watch": pie_watch,
    }

    Path(args.out).write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
