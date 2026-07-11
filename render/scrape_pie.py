"""Scrape current pie flavors from Magpies' Square Online ordering site
(the "Weekend MAPGIES Pre-Orders (Delancey, NY)" category).

Only called by fetch_data.py's fetch_pie_watch() when the weekly cache is
stale -- see PIE_CACHE_PATH / current_pie_week_start() there.

Selector confirmed from a real DOM inspection (2026-07): each product is a
".item_card"; the title lives at "[wrapperid='order-product-title']" inside
it -- that's a deliberate attribute Square's site sets, not a hashed build
class, so it should be more stable than most of the surrounding markup.
Source text is stored in all caps (e.g. "LOCAL BROWN BUTTER BLUEBERRY");
converted to sentence case for display.
"""
from playwright.sync_api import sync_playwright

CATEGORY_URL = (
    "https://magpies-on-pink-street.square.site"
    "/shop/weekend-mapgies-pre-orders-delancey-ny/FOVTQQBLVCTXF6D43SVIHWTI"
    "?location=L2RV28RBZDV92"
)


def scrape_pies() -> list[str]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(CATEGORY_URL, wait_until="networkidle", timeout=30000)
            # The SPA's client-side product fetch can lag slightly past
            # "networkidle" -- give it a beat.
            page.wait_for_timeout(2000)
            return _extract_product_names(page)
        finally:
            browser.close()


def _extract_product_names(page) -> list[str]:
    cards = page.locator(".item_card")
    count = cards.count()
    if count == 0:
        print("[scrape_pie] no .item_card elements found -- site markup may have changed")
        return []

    names = []
    for i in range(count):
        title_locator = cards.nth(i).locator("[wrapperid='order-product-title']")
        if title_locator.count() == 0:
            continue
        text = title_locator.first.inner_text().strip()
        if text:
            names.append(text.capitalize())

    print(f"[scrape_pie] found {len(names)} product name(s) in {count} card(s)")
    return names


if __name__ == "__main__":
    for pie in scrape_pies():
        print(pie)
