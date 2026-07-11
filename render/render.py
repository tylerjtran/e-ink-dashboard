"""Render the dashboard template + data into a PNG at 800x480.

Usage:
    python render.py [--data data.json] [--out output/dashboard.png]
"""
import argparse
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
ASSETS = HERE / "assets"
WIDTH, HEIGHT = 800, 480


def render_html(data: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(HERE)))
    template = env.get_template("template.html.j2")
    # Absolute file:// URI, not a relative path -- the rendered HTML can end
    # up written to a different directory than render/ (e.g. output/), so a
    # relative "assets/..." src wouldn't reliably resolve.
    plant_qr_uri = (ASSETS / "plant_qr_code.png").as_uri()
    return template.render(**data, plant_qr_uri=plant_qr_uri)


def html_to_png(html: str, out_path: Path) -> None:
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html_path = out_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1)
        page.goto(html_path.as_uri())
        page.wait_for_timeout(200)  # let web fonts finish loading
        page.screenshot(path=str(out_path))
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(HERE / "sample_data.json"))
    parser.add_argument("--out", default=str(HERE / "output" / "dashboard.png"))
    args = parser.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    html = render_html(data)
    html_to_png(html, Path(args.out))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
