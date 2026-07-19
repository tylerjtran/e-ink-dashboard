# Setup

## How this works

1. `.github/workflows/refresh-dashboard.yml` pulls data from a handful of
   APIs (`render/fetch_data.py`), renders it into an 800x480 image
   (`render/render.py`), converts that to the raw 1-bit buffer format the
   e-paper panel wants (`render/convert.py`), and commits the result to
   `dashboard/latest.bin` (and `latest.png`, for previewing in a browser).
   It only has a `workflow_dispatch` trigger (no native `schedule` -- GitHub's
   own cron for Actions is best-effort and gets throttled to roughly hourly
   regardless of the cron expression, so it wasn't a reliable fallback
   anyway), which means it does nothing on its own -- see section 2, which
   is required, not optional, for the dashboard to refresh automatically.
2. The Pico 2 W (`firmware/main.py`) connects to Wi-Fi, downloads
   `dashboard/latest.bin` over HTTPS, and pushes the bytes straight into the
   e-paper display. It does no rendering itself.

All API keys and calendar access live only as GitHub Actions secrets -- they
never touch the device or the repo contents, which is important since this
repo is public.

## 1. GitHub secrets

In the repo: **Settings > Secrets and variables > Actions > New repository
secret**. Add these four:

| Secret name | Where to get it |
|---|---|
| `AMBIENT_WEATHER_API_KEY` | ambientweather.net account > Settings > API Keys. This key is tied to *your account*. |
| `AMBIENT_WEATHER_APPLICATION_KEY` | Same page -- a separate "application key" you generate once, identifies this project rather than your account. |
| `AMBIENT_WEATHER_MAC` | The MAC address of your weather station, shown on the device list in your Ambient Weather dashboard. |
| `GOOGLE_CALENDAR_ICAL_URL` | In Google Calendar: Settings > (your birthdays calendar) > "Secret address in iCal format". Copy that whole URL. Keep it secret -- anyone with it can read the calendar. |

Until these are set, the dashboard still renders: weather falls back to
Open-Meteo only (no indoor temp), and birthdays shows nothing.

## 2. Setting up automatic refreshes (required)

`refresh-dashboard.yml` only has a `workflow_dispatch` trigger -- there's no
native `schedule` trigger, so nothing runs it automatically on its own.
(GitHub's own Actions cron is best-effort and gets throttled to roughly
hourly regardless of the cron expression used, so it wasn't a reliable
fallback anyway -- not worth keeping around just to occasionally paper over
an outage in the setup below.) An external service needs to call GitHub's
API to trigger the workflow on a schedule:

1. Create a **fine-grained GitHub personal access token**: github.com >
   Settings > Developer settings > Personal access tokens > Fine-grained
   tokens > Generate new token.
   - Repository access: **Only select repositories** > this repo. Don't
     grant access to any other repo.
   - Permissions: **Actions: Read and write**. Nothing else needed.
   - Set an expiration (e.g. 1 year) rather than "No expiration".
   - Copy the token somewhere safe -- GitHub only shows it once.
2. Sign up for a free scheduling service that can make an authenticated
   HTTP request on a schedule -- e.g. [cron-job.org](https://cron-job.org)
   (free tier supports intervals down to 1 minute, and per-job crontab
   expressions in the job's own timezone). Set up a job:
   - URL: `https://api.github.com/repos/tylerjtran/e-ink-dashboard/actions/workflows/refresh-dashboard.yml/dispatches`
   - Method: `POST`
   - Headers: `Authorization: Bearer <your token>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`
   - Body: `{"ref": "main"}`
   - Schedule: a single cron expression can't express two different
     intervals, so this is actually **two jobs** with identical URL/method/
     headers/body, timezone set to America/New_York:
     - Daytime (every 20 min, 7am-9pm): `0,20,40 7-20 * * *`
     - Overnight (every 2 hours, 9pm-7am): `0 21,23,1,3,5 * * *`
3. Trigger each one manually (most services have a "run now" / "test"
   button) and confirm a new run shows up in the repo's **Actions** tab
   within a few seconds.

That token can trigger workflow runs on this repo, so treat it like any
other credential: don't paste it anywhere besides that one service's
config, and revoke/regenerate it (same Settings page) if you ever suspect
it leaked. There's no `schedule` fallback anymore -- if cron-job.org has an
outage, the token expires, or either job gets accidentally disabled, the
dashboard stops refreshing entirely and silently shows stale data until
someone notices and checks cron-job.org's dashboard for both jobs.

## 3. Editing content that changes over time

- **Business hours** (`render/config/business_hours.yaml`) -- plain text,
  edit and commit directly. No code changes needed, including for seasonal
  hour changes.
- **Location / reservoir normals / meteor showers** all live in
  `render/config/*.yaml` and are hand-edited too.
- **Annual burn ban dates** (`ANNUAL_BURN_BAN_START` / `ANNUAL_BURN_BAN_END`
  in `fetch_data.py`) -- currently March 16 - May 14, NY's statutory
  window. Only needs editing if that regulation changes.
- **Weather climate normals** (`render/climate_normals_cache.json`) are
  *generated*, not hand-edited -- re-run `python generate_climate_normals.py`
  (from the `render/` folder, same venv as the rest of the pipeline) if you
  ever want to shift the 30-year reference period. Not part of the regular
  15-min pipeline; this file only changes when you deliberately regenerate
  it.

## 4. Flashing the Pico 2 W

1. Install MicroPython: hold BOOTSEL, plug in the Pico 2 W, drag the
   [Pico 2 W UF2](https://micropython.org/download/RPI_PICO2_W/) onto the
   drive that appears.
2. Open the `firmware/` folder in Thonny (or use `mpremote`).
3. Copy `firmware/secrets_template.py` to `firmware/secrets.py` and fill in
   your Wi-Fi SSID/password. This file is gitignored on purpose -- never
   commit it.
4. Upload to the Pico: `main.py`, `epd7in5.py`, `secrets.py`, and the whole
   `lib/` folder (containing `requests.py`).
5. Before trusting `main.py`, run `epd7in5.py` directly on the device (e.g.
   via Thonny's "Run current script") -- it's Waveshare's own hardware demo
   and will draw test text/shapes on the panel, confirming the wiring and
   SPI pins are good independent of Wi-Fi/network code.
6. Once that works, run `main.py`. It loops forever: connect Wi-Fi, fetch
   `dashboard/latest.bin`, display it (skipping the physical refresh if the
   content hasn't changed since last time), sleep 5 minutes, repeat.

If you rename or fork the repo, update `IMAGE_URL` in `firmware/main.py`.

## 5. Local dev (testing the render pipeline without hardware)

```
cd render
python -m venv .venv
.venv/Scripts/activate   # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python -m playwright install chromium

python fetch_data.py --out data.json      # pulls live data
python render.py --data data.json --out output/dashboard.png
python convert.py --in output/dashboard.png --out output/dashboard.bin
```

`fetch_data.py` degrades gracefully without the GitHub secrets set locally
(Ambient Weather and birthdays just come back empty) -- useful for testing
the rest of the pipeline without setting up local credentials. If you want
to test those two, export the same four env vars locally before running it.

## 6. Known gaps / things to double check once hardware is running

- **Pepacton Reservoir % full**: fixed -- this used to come from a
  mislabeled field in NYC's Socrata "Current Reservoir Levels" dataset and
  was confirmed wrong (74% vs. the real ~89%). It's now scraped directly
  from https://www.nyc.gov/site/dep/water/reservoir-levels.page, which
  server-renders the exact numbers DEP shows the public. That page returns
  403 without a browser-like `User-Agent` header, which `fetch_nyc_reservoir()`
  sets -- if DEP ever changes their page layout, the regex in that function
  will need updating (it fails soft, logging a warning and showing "—").
- **River temperature**: fixed -- switched to USGS site 01417500 (East
  Branch Delaware at Harvard, NY), which actively reports temperature
  (01417000 at Downsville didn't). The "warmer/colder than normal" line for
  the river comes from USGS's actual daily-statistics service (decades of
  median-by-day-of-year data via `nwis/stat`), not a hand-maintained table
  like the weather/reservoir normals below -- this one's the real thing.
- **Weather/reservoir "normal" comparisons** are rough monthly-average
  tables (`render/config/settings.yaml`), not official daily normals --
  good enough for a ballpark "warmer/lower than normal" line, not precise.
- **Pie Watch** (`render/scrape_pie.py`) scrapes Magpies' plain Square
  Online order page (`ORDER_URL`, no specific category path -- letting the
  site show whatever's currently available rather than hardcoding a
  category ID, since an earlier attempt at that got redirected away from
  the category by the site itself). The `[wrapperid='order-product-title']`
  selector has been confirmed working against real Action-run logs -- it
  found and correctly extracted 3 real pie names. Still worth keeping an
  eye on, since Square could change their markup at any time, but this
  isn't a guess anymore. It fails soft either way (falls back to the last
  good cached list, or "Check back soon" if there's never been one), so a
  future broken selector won't break the dashboard, it'll just keep showing
  stale/placeholder content and retry on the next run.
- **Pie Watch confirm-different-content logic**: after blackout ends (Tue
  3pm), a successful scrape that returns the *same* list as what's already
  cached is deliberately **not** confirmed as the new week's menu -- it's
  treated as "site hasn't posted this week's update yet," and it retries
  every `PIE_RETRY_INTERVAL` (3 hours, in `fetch_data.py`) until the result
  actually changes. Check `render/pie_cache.json`'s `confirmed_week_start`
  field to see whether this week has actually been confirmed yet, and the
  "Fetch data" step logs for `[pie_watch] scraped pies match the cached
  list` if it's still waiting. Only tries during Tue 3pm - Sun 2pm -- see
  `current_pie_week_start()` / `in_pie_blackout()` in `fetch_data.py`.
- **Game Watch** (Phillies/Eagles/Sixers/Flyers) hasn't been tested against
  a real live in-progress game yet for any of them -- worth double checking
  once a real game happens. Eagles/Sixers/Flyers use ESPN's public site API
  (`site.api.espn.com`), which is unauthenticated and free but undocumented/
  unofficial -- it could change shape without notice. Phillies still uses
  the official MLB Stats API.
- **Game Watch caching**: each team's schedule is only re-fetched once per
  day if there's no game that day (`render/game_watch_cache.json`, see
  README). If a team's status ever looks stuck on a stale "next game"
  message past when it should've updated (e.g. a same-day doubleheader
  makeup game got added after the day's first check), delete that team's
  entry from the cache file to force a re-check, or just wait for the next
  calendar day to roll the cache over automatically.
- **Burn ban / fire risk** (`get_burn_ban_str` in `fetch_data.py`): the
  fire-risk half (outside the Mar 16-May 14 statutory window) has been
  confirmed against live data -- it correctly pulled "Catskill: Low" from
  the Mesonet table. The burn-ban half (inside that window) hasn't been
  tested against a real run during that window yet since it was built in
  July, though the date-range logic itself is simple enough to trust. If
  NY's Part 215 dates ever change, update `ANNUAL_BURN_BAN_START`/`_END`.
