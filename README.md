# e-ink-dashboard

A 7.5" (800x480) e-paper dashboard for a house in Downsville, NY. Shows
weather, stargazing conditions, river/reservoir levels, local plant
observations, nearby business hours, birthdays, Phillies/Eagles/Sixers/Flyers
game status, and a pie-watch box for the local bakery.

## How it works

- **GitHub Actions** (`.github/workflows/refresh-dashboard.yml`) fetches
  data from various APIs, renders it to an 800x480 PNG, converts that to the
  raw byte format the display wants, and commits both to `dashboard/`.
  Meant to run every ~15 minutes, but GitHub's own scheduling for Actions is
  best-effort and gets throttled to roughly hourly in practice -- see
  SETUP.md for an optional external trigger to get real 15-minute updates.
- **Raspberry Pi Pico 2 W** (`firmware/`) connects to Wi-Fi and downloads
  `dashboard/latest.bin` on its own independent 15-minute timer, pushing it
  straight to a Waveshare 7.5" e-paper panel. It does no rendering of its
  own, and its timer isn't synced to the Action's -- it just checks
  whatever's currently committed each time it wakes up.

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

## Error handling

Two layers:

- **Per-box isolation**: nearly every external call in `fetch_data.py`
  catches its own failures and degrades gracefully -- a box shows "—" or a
  placeholder rather than breaking the run. Notably, Weather has two
  independent sources (Open-Meteo, Ambient Weather) that can each fail
  without affecting the other, and each of the 4 Game Watch teams fails
  independently (if ESPN is down but MLB isn't, Phillies still shows real
  data while Eagles/Sixers/Flyers show "–").
- **Fail-closed pipeline**: `main()` only writes `data.json` after every box
  has been fetched (degraded or not), and `render.py`/`convert.py` have no
  error handling of their own. If something raises anyway (a bug, a config
  typo, Playwright failing to launch), that whole run aborts and nothing
  gets committed -- the dashboard keeps showing its last successfully
  rendered image rather than a broken or half-updated one. Given the
  15-minute cadence, a transient failure usually just delays the next
  update by one cycle; a sustained one leaves the dashboard stale until it
  clears (GitHub emails the repo owner by default on repeated scheduled-run
  failures, but there's no other alerting built here).

## Data sources & logic

All fetching/computation lives in `render/fetch_data.py` unless noted.
Config (locations, hours, thresholds) lives in `render/config/*.yaml`.

**Layout note**: Birthday Watch, Game Watch, and Pie Watch have genuinely
variable-length content (more simultaneous live games, more birthdays, more
pies). `render/render.py`'s `fit_variable_boxes()` measures each box's real
content height in the browser (before the final screenshot) and sizes them
to fit exactly, instead of relying on hand-picked flex ratios that only
happen to match today's content. If content ever exceeds the available
space in the 800x480 canvas (a hard physical limit -- the display can't
scroll), it shrinks that content's text proportionally rather than clipping
or overflowing, and logs a warning.

**Weather**
- Outdoor + indoor temp: Ambient Weather Network (your station), falling
  back to Open-Meteo's outdoor temp (no indoor reading) if those secrets
  aren't set *or* if the Ambient Weather call fails at runtime.
- Condition, chance of rain, high/low: Open-Meteo forecast, mapped from WMO
  weather codes to text (`WMO_CODE_TEXT`).
- Open-Meteo and Ambient Weather are fetched independently and can each fail
  without affecting the other (see "Error handling" above) -- if Open-Meteo
  is down, temp still shows from Ambient Weather (or "—" if that's also
  unavailable), while condition/rain%/high/low show "Unknown"/"—".
- "X° warmer/cooler than normal": forecast daily mean temp vs. a real
  30-year (1991-2020) historical day-of-year average -- not an
  approximation like the reservoir/weather normals elsewhere. Computed once
  by `render/generate_climate_normals.py` (not part of the regular
  pipeline) from Open-Meteo's historical archive API, and stored in
  `render/climate_normals_cache.json`. The regular pipeline only ever does
  a local lookup against that file -- no network call for this metric on
  any normal run, since a fixed 30-year baseline doesn't change day to day.
  Re-run the generator script by hand if you ever want to shift the
  reference period (e.g. to a more recent 30 years).
- "X° warmer/cooler than Philly": your resolved outdoor temp vs. a separate
  live Open-Meteo call for Philadelphia's coordinates.
- Burn ban / fire risk (`get_burn_ban_str`): shows "Burn ban in place"
  during NY's statutory annual open-burning restriction (March 16 - May 14,
  hardcoded, 6 NYCRR Part 215 -- no data source needed, and the fire-risk
  API below isn't even called during this window). Outside that window,
  shows "[Low/Moderate/High/etc.] fire risk" -- scraped from the NY State
  Mesonet's FDRA risk table (`api.nysmesonet.org`, the same source NY DEC's
  own fire danger map embeds via iframe), filtered to the "Catskill" region
  (covers Downsville/Delaware County). Cached once per calendar day
  (`render/fire_risk_cache.json`) since that table only issues a new
  ~2-day "Effective" window about once a day.

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
- Reservoir data is cached once per calendar day
  (`render/reservoir_cache.json`, committed by the Action like the other
  caches) rather than scraped every ~15-min run -- DEP's page itself only
  updates about once a day, so more frequent scraping would just hit the
  same number repeatedly. River temp is *not* cached this way since it's a
  live instantaneous reading that actually changes through the day. A
  failed reservoir fetch falls back to the last cached value even if it's a
  day or two stale, rather than showing "—" -- this data moves slowly
  enough that stale still beats blank.

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
- The QR code in the corner (linking to the iNaturalist project) is a
  static image, `render/assets/plant_qr_code.png` -- not generated at
  render time.

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
  recurs). Otherwise shows the box as "Birthday Watch" with up to the
  nearest 2 upcoming birthdays within the next 30 days (none shown if
  nothing falls in that window).

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
- Cached per team in `render/game_watch_cache.json` (committed by the
  Action, like the pie cache): the first check each day determines whether
  that team has a game today (live or scheduled). If not, later checks that
  same day reuse the cached result instead of re-fetching -- the answer
  ("next game is in 3 days") won't change between 15-minute runs anyway. If
  there is a game today, every run re-fetches so live status (inning,
  quarter, period) stays current. A failed fetch falls back to that team's
  cached result from earlier today rather than going blank.

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
