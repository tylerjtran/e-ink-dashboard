# e-ink-dashboard

A 7.5" (800x480) e-paper dashboard for a house in Downsville, NY. Shows
weather, stargazing conditions, river/reservoir levels, local plant
observations, nearby business hours, birthdays, Phillies/Eagles/Sixers game
status, and a pie-watch box for the local bakery.

## How it works

- **GitHub Actions** (`.github/workflows/refresh-dashboard.yml`) runs every
  ~15 minutes: fetches data from various APIs, renders it to an 800x480 PNG,
  converts that to the raw byte format the display wants, and commits both
  to `dashboard/`.
- **Raspberry Pi Pico 2 W** (`firmware/`) connects to Wi-Fi and downloads
  `dashboard/latest.bin` on the same ~15 minute cadence, pushing it straight
  to a Waveshare 7.5" e-paper panel. It does no rendering of its own.

See [docs/SETUP.md](docs/SETUP.md) for how to configure secrets, edit
content like business hours, and flash the Pico -- including known gaps and
things worth double-checking. This doc covers where each box's data comes
from and how it's computed.

## Layout

```
render/     data fetching + HTML/CSS template + image rendering (Python)
firmware/   MicroPython code that runs on the Pico 2 W
dashboard/  latest rendered output, published by CI
docs/       setup instructions
```

## Data sources & logic

All fetching/computation lives in `render/fetch_data.py` unless noted.
Config (locations, hours, thresholds) lives in `render/config/*.yaml`.

**Weather**
- Outdoor + indoor temp: Ambient Weather Network (your station), falling
  back to Open-Meteo's outdoor temp (no indoor reading) if those secrets
  aren't set.
- Condition, chance of rain, high/low: Open-Meteo forecast, mapped from WMO
  weather codes to text (`WMO_CODE_TEXT`).
- "X° warmer/cooler than normal": forecast high vs. a hand-maintained
  monthly climate-normals table (`climate_normals` in `settings.yaml`) --
  approximate, not official daily normals.
- "X° warmer/cooler than Philly": your resolved outdoor temp vs. a separate
  live Open-Meteo call for Philadelphia's coordinates.
- Burn ban: manual toggle (`burn_ban_active` in `settings.yaml`) -- no
  public API found for NY DEC burn ban status.

**Stargazing**
- Conditions ("Excellent/Good/OK/Poor"): a score combining forecast cloud
  cover for *tonight* specifically (Open-Meteo hourly, averaged over 9-11pm,
  not whatever the sky looks like at fetch time) and moon illumination --
  more cloud/more moon brightness both lower the rating.
- Moon phase name and illumination %: computed locally (`astral` library),
  not fetched from anywhere.
- Meteor shower heads-up ("X peaks in N days"): checked against a
  hand-maintained annual calendar (`config/meteor_showers.yaml`) of known
  peak dates, not a live feed.

**River & Reservoir**
- River temp: USGS site 01417500 (East Branch Delaware at Harvard, NY),
  live instantaneous-values API.
- River "warmer/colder than normal": USGS's own daily-statistics service --
  a real decades-long median for that specific day-of-year, not an
  approximation.
- Reservoir % full and "higher/lower than normal": scraped directly from
  DEP's public reservoir-levels page (server-rendered HTML, not a JSON
  API). The "normal" comparison is a hand-maintained approximate seasonal
  curve (`nyc_reservoir.normal_pct_by_month`), unlike the river's.

**Plant Watch**
- iNaturalist species_counts for the [Boy Scout Road
  project](https://www.inaturalist.org/projects/boy-scout-road), filtered to
  `iconic_taxa=Plantae` (excludes animal observations in the same project)
  and to species-level taxon rank only (excludes genus/family-level IDs like
  "Rubus" with no species determined).
- "New species this month": species whose *observation* date (when the
  sighting happened, not when it was added/IDed in iNaturalist) falls after
  the start of the current month -- computed as a set difference between
  all-time species and species observed before this month.

**Business Watch**
- Purely config-driven: `config/business_hours.yaml`, checked against the
  current day/time. No external calls. Edit that file directly to add
  businesses or change hours.

**Birthday Watch**
- A secret iCal feed for a dedicated Google Calendar (Google's
  auto-generated "Birthdays" calendar doesn't support iCal export, so this
  is a manually-maintained calendar instead -- see SETUP.md).
- Shows today's birthdays if any exist that day (matched by month/day,
  ignoring year -- works whether or not the calendar event actually
  recurs). Otherwise shows the box as "Birthday Watch" with up to the next
  3 upcoming birthdays and their dates.

**Game Watch**
- Phillies: official MLB Stats API -- checks for a live game first, then
  the next scheduled one in the following 30 days.
- Eagles / Sixers / Flyers: ESPN's public site API (`site.api.espn.com`) --
  same live-then-next-scheduled logic, using each team's full season
  schedule and computing "today"/"tomorrow"/date relative to now. This API
  is free and unauthenticated but undocumented/unofficial -- it could
  change shape without notice, unlike the official MLB one. Live-game
  wording says "quarter" for football/basketball, "period" for hockey.
- Any team shows as "Team: –" if no live or upcoming game is found (e.g.
  NBA/NFL off-season before the next schedule is published).

**Pie Watch**
- Blackout window (Sun 2pm - Tue 9am): shows a static "Stay tuned..."
  message, no scraping attempted.
- Otherwise: Playwright scrapes Magpies' Square Online order page
  (`render/scrape_pie.py`) for current product names. Runs at most once per
  "pie week" (a fresh scrape is only attempted once the cached result is
  older than the most recent Tuesday 9am) and caches the result in
  `render/pie_cache.json`, which the Action commits back to the repo. A
  failed scrape falls back to the last cached list rather than showing
  nothing.

**Electric note**
- Config-driven peak/off-peak schedule (`electric` in `settings.yaml`), no
  external calls.
