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
    # Absolute file:// URIs, not relative paths -- the rendered HTML can end
    # up written to a different directory than render/ (e.g. output/), so a
    # relative "assets/..." src wouldn't reliably resolve.
    return template.render(
        **data,
        plant_qr_uri=(ASSETS / "plant_qr_code.png").as_uri(),
        open_window_uri=(ASSETS / "open_window.png").as_uri(),
        closed_window_uri=(ASSETS / "closed_window.png").as_uri(),
    )


# Boxes whose content length varies run to run (more/fewer live games,
# birthdays, pies) rather than being fixed by design (unlike e.g. Business
# Watch, which is one line per configured business). Their template flex
# values are just a reasonable starting point/fallback -- _fit_variable_boxes
# overrides them based on actually-measured content each render.
VARIABLE_CONTENT_BOX_IDS = ["birthdays", "game-watch", "pie-watch"]

# JS, not Python: needs real layout/measurement (scrollHeight, computed
# styles) that only exist in the rendered page.
_FIT_VARIABLE_BOXES_JS = """
(boxIds) => {
    const boxes = boxIds.map(id => document.getElementById(id));
    const column = boxes[0].parentElement;

    const naturalHeight = (box) => {
        const title = box.querySelector('.box-title');
        const body = box.querySelector('.box-body');
        const style = getComputedStyle(box);
        const paddingV = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
        const borderV = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
        const titleMarginBottom = parseFloat(getComputedStyle(title).marginBottom) || 0;
        // scrollHeight reflects the box-body's true content height even
        // while overflow:hidden is clipping it visually -- that's exactly
        // what we need to measure before deciding how much room to give it.
        return title.offsetHeight + titleMarginBottom + body.scrollHeight + paddingV + borderV;
    };

    const heights = boxes.map(naturalHeight);
    const gap = parseFloat(getComputedStyle(column).rowGap) || 0;
    const totalNatural = heights.reduce((a, b) => a + b, 0) + gap * (boxes.length - 1);
    const available = column.clientHeight;

    if (totalNatural <= available) {
        // Fits: size each box to exactly what its content needs (no grow,
        // no shrink -- content-driven, not an arbitrary ratio), and let the
        // last box absorb any leftover column space so the column still
        // fills its height instead of leaving a stray gap at the bottom.
        boxes.forEach((box, i) => {
            const grow = i === boxes.length - 1 ? 1 : 0;
            box.style.flex = `${grow} 0 ${heights[i]}px`;
        });
        return { fit: true, totalNatural, available };
    }

    // Doesn't fit -- shrink these boxes' text proportionally rather than
    // let CSS silently clip content or push a box off the bottom of the
    // canvas. Rare in practice (birthdays are capped, game watch is always
    // exactly 4 rows), but this is the graceful fallback if it ever happens.
    const scale = available / totalNatural;
    boxes.forEach((box, i) => {
        const body = box.querySelector('.box-body');
        const currentSize = parseFloat(getComputedStyle(body).fontSize);
        body.style.fontSize = `${(currentSize * scale).toFixed(2)}px`;
        box.style.flex = `0 0 ${(heights[i] * scale).toFixed(2)}px`;
    });
    return { fit: false, totalNatural, available, scale };
}
"""


def fit_variable_boxes(page) -> None:
    result = page.evaluate(_FIT_VARIABLE_BOXES_JS, VARIABLE_CONTENT_BOX_IDS)
    if not result["fit"]:
        print(
            f"[render] WARNING: right-column content ({result['totalNatural']:.0f}px) exceeded "
            f"available height ({result['available']:.0f}px) -- shrank text by "
            f"{result['scale']:.2f}x to fit. Consider capping content further "
            f"(e.g. fewer upcoming birthdays/pies) if this happens often."
        )


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
        fit_variable_boxes(page)
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
