"""Scrape current pie flavors from Magpies' Square Online ordering site.

Only called by fetch_data.py's fetch_pie_watch() when the weekly cache is
stale -- see PIE_CACHE_PATH / current_pie_week_start() there.

ORDER_URL is the plain order page for this location, no specific category
path -- an earlier version tried to target the "Weekend MAPGIES Pre-Orders
(Delancey, NY)" category directly, but the site redirected away from it to a
different category ("Pre-Orders at Magpies") that actually had items. The
plain URL lets the site pick whatever's currently available itself, which
also makes this more robust to Square reshuffling their category IDs.

Selector confirmed against real Action-run output (2026-07): product titles
are "[wrapperid='order-product-title']" -- a deliberate attribute Square's
site sets, not a hashed build class, so it should be reasonably stable. (An
earlier ".item_card" wrapper selector, based on a DOM inspection of a
product detail popup, matched 0 elements on this list view -- dropped.)
Source text is stored in all caps (e.g. "LOCAL BROWN BUTTER BLUEBERRY");
converted to sentence case for display.
"""
from playwright.sync_api import sync_playwright

ORDER_URL = "https://magpies-on-pink-street.square.site/s/order?location=L2RV28RBZDV92"


def scrape_pies() -> list[str]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(ORDER_URL, wait_until="networkidle", timeout=30000)
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
    titles = page.locator("[wrapperid='order-product-title']")
    count = titles.count()
    if count == 0:
        _log_diagnostics(page)
        return []

    names = []
    for i in range(count):
        text = titles.nth(i).inner_text().strip()
        if text:
            names.append(text.capitalize())

    print(f"[scrape_pie] found {len(names)} product name(s): {names}")
    return names


def _log_diagnostics(page):
    """No product titles found -- log everything useful for figuring out why
    without needing another round-trip of "here's some HTML"."""
    print("[scrape_pie] no product titles found -- diagnostics follow")
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
    for selector in ["[wrapperid='order-product-title']", ".item_card", "main"]:
        try:
            n = page.locator(selector).count()
            print(f"[scrape_pie]   count of {selector!r}: {n}")
        except Exception as e:
            print(f"[scrape_pie]   count of {selector!r} failed: {e}")


if __name__ == "__main__":
    for pie in scrape_pies():
        print(pie)
