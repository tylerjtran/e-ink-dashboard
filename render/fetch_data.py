"""Pull all dashboard data from external sources and write data.json.

Run with the same Python env used for render.py:
    python fetch_data.py [--out data.json]

Requires these environment variables (see ../docs/SETUP.md):
    AMBIENT_WEATHER_API_KEY
    AMBIENT_WEATHER_APPLICATION_KEY
    AMBIENT_WEATHER_MAC
    GOOGLE_CALENDAR_ICAL_URL
"""
import argparse
import json
import os
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from astral import moon
from icalendar import Calendar

HERE = Path(__file__).parent
CONFIG = HERE / "config"
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
    loc = settings["location"]
    om = settings["open_meteo"]
    params = {
        "latitude": loc["lat"],
        "longitude": loc["lon"],
        "current": "temperature_2m,cloud_cover,weather_code",
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

    high_f = round(daily["temperature_2m_max"][0])
    low_f = round(daily["temperature_2m_min"][0])
    weather_code = current.get("weather_code", daily["weather_code"][0])
    condition = WMO_CODE_TEXT.get(weather_code, "Unknown")
    chance_rain_pct = daily["precipitation_probability_max"][0]
    cloud_cover_pct = current.get("cloud_cover", 0)

    outdoor_temp_f = round(current["temperature_2m"])
    indoor_temp_f = None
    try:
        aw = fetch_ambient_weather(settings)
        if aw:
            outdoor_temp_f = round(aw["tempf"])
            indoor_temp_f = round(aw["tempinf"])
    except Exception as e:
        print(f"[warn] Ambient Weather fetch failed, using Open-Meteo temp only: {e}")

    normal_diff_str = compute_normal_diff(settings, high_f)

    return {
        "temp_f": outdoor_temp_f,
        "condition": condition,
        "chance_rain_pct": chance_rain_pct,
        "high_f": high_f,
        "low_f": low_f,
        "normal_diff_str": normal_diff_str,
        "indoor_temp_f": indoor_temp_f if indoor_temp_f is not None else outdoor_temp_f,
        "burn_ban_str": "Burn ban in effect" if settings["burn_ban_active"] else "No burn ban",
        "icon_name": weather_icon_name(weather_code),
        "_cloud_cover_pct": cloud_cover_pct,  # used by skygazing, not displayed directly
    }


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
    return "Near normal"


# -------------------------------------------------------------- skygazing --

def fetch_skygazing(settings, cloud_cover_pct):
    today = date.today()
    phase_value = moon.phase(today)  # 0-27.99, 0/14 = new/full
    phase_name = moon_phase_name(phase_value)
    illumination_pct = moon_illumination_pct(phase_value)

    conditions_str = f"{rate_sky_conditions(cloud_cover_pct, illumination_pct)} conditions."
    moon_phase_str = f"{phase_name}."

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
    river_temp_f = fetch_usgs_river_temp(settings)
    reservoir_pct_full, reservoir_note = fetch_nyc_reservoir(settings)

    return {
        "river_name": "East Branch of the Delaware",
        "river_temp_f": river_temp_f if river_temp_f is not None else "—",
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
        if not series:
            return None
        values = series[0]["values"][0]["value"]
        if not values:
            return None
        temp_c = float(values[-1]["value"])
        return round(temp_c * 9 / 5 + 32)
    except Exception as e:
        print(f"[warn] USGS river temp fetch failed: {e}")
        return None


def fetch_nyc_reservoir(settings):
    cfg = settings["nyc_reservoir"]
    try:
        r = requests.get(
            cfg["dataset_url"],
            params={"$order": "neversink_date DESC", "$limit": 1},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        row = r.json()[0]
        storage_bg = float(row[cfg["pepacton_storage_field"]])
        pct_full = round(storage_bg / cfg["pepacton_capacity_billion_gallons"] * 100)

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
    params = {"project_id": project_slug, "per_page": 200}
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

def fetch_birthdays(today):
    ical_url = os.environ.get("GOOGLE_CALENDAR_ICAL_URL")
    if not ical_url:
        return []
    try:
        r = requests.get(ical_url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        cal = Calendar.from_ical(r.content)
        names = []
        for component in cal.walk("VEVENT"):
            dtstart = component.get("dtstart")
            if not dtstart:
                continue
            d = dtstart.dt
            if hasattr(d, "date"):
                d = d.date()
            if d.month == today.month and d.day == today.day:
                names.append(str(component.get("summary")))
        return names
    except Exception as e:
        print(f"[warn] Birthday calendar fetch failed: {e}")
        return []


# ---------------------------------------------------------------- phillies --

def fetch_phillies(settings, now):
    team_id = settings["mlb"]["team_id"]
    short_name = settings["mlb"]["team_short_name"]
    today_str = now.strftime("%Y-%m-%d")

    try:
        r = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "teamId": team_id, "date": today_str},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        games = r.json().get("dates", [])
        games = games[0]["games"] if games else []

        if games:
            game = games[0]
            state = game["status"]["abstractGameState"]
            opponent = _opponent_name(game, team_id)
            if state == "Live":
                return _live_phillies_line(game, opponent)
            if state == "Final":
                return _final_phillies_line(game, opponent, team_id, short_name)
            # Preview / scheduled later today
            game_time = _format_game_time(game["gameDate"], settings["location"]["timezone"])
            return {"line1": f"Next game vs {opponent} today at {game_time}", "line2": None}

        return _next_scheduled_game(settings, now, team_id)
    except Exception as e:
        print(f"[warn] MLB Stats API fetch failed: {e}")
        return {"line1": "—", "line2": None}


def _opponent_name(game, team_id):
    teams = game["teams"]
    other = teams["away"] if teams["home"]["team"]["id"] == team_id else teams["home"]
    return other["team"]["name"].split()[-1]  # e.g. "Atlanta Braves" -> "Braves"


def _format_game_time(game_date_utc, tz_name):
    dt = datetime.fromisoformat(game_date_utc.replace("Z", "+00:00")).astimezone(ZoneInfo(tz_name))
    return fmt_time12(dt)


def _live_phillies_line(game, opponent):
    try:
        game_pk = game["gamePk"]
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        linescore = r.json()["liveData"]["linescore"]
        inning = linescore["currentInning"]
        state = linescore["inningState"]  # "Top", "Bottom", "Middle", "End"
        return {
            "line1": f"Game vs {opponent} in progress",
            "line2": f"{state} {_ordinal(inning)}",
        }
    except Exception as e:
        print(f"[warn] MLB live feed fetch failed: {e}")
        return {"line1": f"Game vs {opponent} in progress", "line2": None}


def _final_phillies_line(game, opponent, team_id, short_name):
    teams = game["teams"]
    home, away = teams["home"], teams["away"]
    is_home = home["team"]["id"] == team_id
    us = home if is_home else away
    them = away if is_home else home
    return {"line1": f"Final: {short_name} {us['score']}, {opponent} {them['score']}", "line2": None}


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _next_scheduled_game(settings, now, team_id):
    end = (now + timedelta(days=21)).strftime("%Y-%m-%d")
    r = requests.get(
        "https://statsapi.mlb.com/api/v1/schedule",
        params={"sportId": 1, "teamId": team_id, "startDate": now.strftime("%Y-%m-%d"), "endDate": end},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    dates = r.json().get("dates", [])
    if not dates:
        return {"line1": "See you next season.", "line2": None}
    game = dates[0]["games"][0]
    opponent = _opponent_name(game, team_id)
    game_dt = datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00")).astimezone(
        ZoneInfo(settings["location"]["timezone"])
    )
    game_time = fmt_time12(game_dt)
    return {"line1": f"Next game vs {opponent} {game_dt:%B} {game_dt.day} at {game_time}", "line2": None}


# --------------------------------------------------------------- pie watch --

def fetch_pie_watch():
    # Placeholder until the Playwright-based scrape of Magpies' site is built.
    return [
        "Check back soon — pie list coming shortly",
    ]


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
    skygazing = fetch_skygazing(settings, weather.pop("_cloud_cover_pct"))
    river_reservoir = fetch_river_reservoir(settings)
    plant_watch = fetch_plant_watch(settings)
    business_watch = fetch_business_watch(now)
    birthdays = fetch_birthdays(today)
    phillies = fetch_phillies(settings, now)
    pie_watch = fetch_pie_watch()
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
        "phillies": phillies,
        "pie_watch": pie_watch,
    }

    Path(args.out).write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
