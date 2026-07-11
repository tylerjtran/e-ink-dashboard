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
            _wait_for_app_ready(page)
            return _extract_product_names(page)
        finally:
            browser.close()


def _wait_for_app_ready(page):
    """The site's own JS removes '.loading-view' once the SPA has finished
    bootstrapping (window.stopSiteLoadingAnimation, seen in the page's
    inline script). Wait for that rather than a fixed sleep -- and log
    whether it actually happened, since that's the single most useful signal
    for diagnosing an empty scrape."""
    try:
        page.wait_for_selector(".loading-view", state="detached", timeout=15000)
        print("[scrape_pie] loading overlay cleared")
    except Exception:
        still_present = page.locator(".loading-view").count() > 0
        print(f"[scrape_pie] loading overlay still present after 15s: {still_present}")
    # Give the product-fetch XHR a beat even after the overlay clears.
    page.wait_for_timeout(2000)


def _extract_product_names(page) -> list[str]:
    cards = page.locator(".item_card")
    count = cards.count()
    if count == 0:
        _log_diagnostics(page)
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


def _log_diagnostics(page):
    """No .item_card found -- log everything useful for figuring out why
    without needing another round-trip of "here's some HTML"."""
    print("[scrape_pie] no .item_card elements found -- diagnostics follow")
    print(f"[scrape_pie]   final URL: {page.url}")
    try:
        print(f"[scrape_pie]   page title: {page.title()}")
    except Exception as e:
        print(f"[scrape_pie]   page title fetch failed: {e}")
    try:
        body_text = page.inner_text("body")
        print(f"[scrape_pie]   body text length: {len(body_text)} chars")
        print(f"[scrape_pie]   body text (first 500 chars): {body_text[:500]!r}")
    except Exception as e:
        print(f"[scrape_pie]   body text fetch failed: {e}")
    for selector in [".item_card", "[wrapperid='order-product-title']", "[data-v-7921ef50]", "article", "main"]:
        try:
            n = page.locator(selector).count()
            print(f"[scrape_pie]   count of {selector!r}: {n}")
        except Exception as e:
            print(f"[scrape_pie]   count of {selector!r} failed: {e}")


if __name__ == "__main__":
    for pie in scrape_pies():
        print(pie)
