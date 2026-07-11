# e-ink-dashboard

A 7.5" (800x480) e-paper dashboard for a house in Downsville, NY. Shows
weather, stargazing conditions, river/reservoir levels, local plant
observations, nearby business hours, birthdays, Phillies game status, and a
pie-watch box for the local bakery.

## How it works

- **GitHub Actions** (`.github/workflows/refresh-dashboard.yml`) runs every
  ~15 minutes: fetches data from various APIs, renders it to an 800x480 PNG,
  converts that to the raw byte format the display wants, and commits both
  to `dashboard/`.
- **Raspberry Pi Pico 2 W** (`firmware/`) connects to Wi-Fi and downloads
  `dashboard/latest.bin` on the same ~15 minute cadence, pushing it straight
  to a Waveshare 7.5" e-paper panel. It does no rendering of its own.

See [docs/SETUP.md](docs/SETUP.md) for how to configure secrets, edit
content like business hours, and flash the Pico.

## Layout

```
render/     data fetching + HTML/CSS template + image rendering (Python)
firmware/   MicroPython code that runs on the Pico 2 W
dashboard/  latest rendered output, published by CI
docs/       setup instructions
```
