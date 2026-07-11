# Setup

## How this works

1. `.github/workflows/refresh-dashboard.yml` runs every ~15 minutes. It pulls
   data from a handful of APIs (`render/fetch_data.py`), renders it into an
   800x480 image (`render/render.py`), converts that to the raw 1-bit buffer
   format the e-paper panel wants (`render/convert.py`), and commits the
   result to `dashboard/latest.bin` (and `latest.png`, for previewing in a
   browser).
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

## 2. Editing content that changes over time

- **Business hours** (`render/config/business_hours.yaml`) -- plain text,
  edit and commit directly. No code changes needed, including for seasonal
  hour changes.
- **Burn ban** (`render/config/settings.yaml`, `burn_ban_active`) -- flip
  this by hand for now; no public API was found for NY DEC burn ban status
  at build time.
- **Location / climate normals / reservoir normals / meteor showers** all
  live in `render/config/*.yaml` and are hand-edited too.

## 3. Flashing the Pico 2 W

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
   `dashboard/latest.bin`, display it, sleep 15 minutes, repeat.

If you rename or fork the repo, update `IMAGE_URL` in `firmware/main.py`.

## 4. Local dev (testing the render pipeline without hardware)

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

## 5. Known gaps / things to double check once hardware is running

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
- **Pie Watch** is a placeholder. The plan is a Playwright scrape of the
  Magpies site/social -- to be designed later.
- **Game Watch** (Phillies/Eagles/Sixers) hasn't been tested against a real
  live in-progress game yet for any of the three -- worth double checking
  once a real game happens. Eagles/Sixers use ESPN's public site API
  (`site.api.espn.com`), which is unauthenticated and free but undocumented/
  unofficial -- it could change shape without notice. Phillies still uses
  the official MLB Stats API.
