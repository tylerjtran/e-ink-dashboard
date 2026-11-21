"""Scrape current pie flavors from Magpies' Square Online ordering site.

Only called by fetch_data.py's fetch_pie_watch() when the weekly cache is
stale -- see PIE_CACHE_PATH / current_pie_week_start() there.

How it works (rewritten 2026-11 after Square changed the site): the plain
order URL now redirects to the full shop ("All Items" -- pies mixed in with
candles, coffee, etc.), and the old `[wrapperid='order-product-title']`
selector no longer exists anywhere on it, which left the scraper returning
nothing for weeks. The weekly pies live in the "Pre-Orders at Magpies"
category, so this now:
  1. loads the shop page,
  2. finds that category's link by its *visible name* (not a hardcoded
     category ID, which Magpies has changed before -- an earlier version
     targeted "Weekend MAPGIES Pre-Orders (Delancey, NY)" by ID and got
     redirected away from it), and follows it,
  3. reads the product titles off that page (`.w-product-title`).
If the category link can't be found it logs what links *were* on the page and
returns [] -- fetch_pie_watch() then falls back to the last cached list.

Source text is stored in all caps (e.g. "LOCAL BROWN BUTTER BLUEBERRY");
converted to sentence case for display.
"""
import re

from playwright.sync_api import sync_playwright

ORDER_URL = "https://magpies-on-pink-street.square.site/s/order?location=L2RV28RBZDV92"

# Matched against the category link's whole visible text, case-insensitively
# -- exact, so "M.P.O. Pre-orders" (a different location's category) and the
# Thanksgiving categories don't match.
CATEGORY_NAME = "Pre-Orders at Magpies"

PRODUCT_TITLE_SELECTOR = ".w-product-title"


def scrape_pies() -> list[str]:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(ORDER_URL, wait_until="networkidle", timeout=30000)
            _wait_for_app_ready(page)
            if not _open_category(page):
                return []
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


def _open_category(page) -> bool:
    """Navigate to the weekly pre-order category. Returns False (after
    logging diagnostics) if its link isn't on the page."""
    href = page.evaluate(
        """(name) => {
            const want = name.trim().toLowerCase();
            const link = [...document.querySelectorAll('a[href*="/shop/"]')]
                .find(a => a.innerText.trim().toLowerCase() === want);
            return link ? link.href : null;
        }""",
        CATEGORY_NAME,
    )
    if not href:
        print(f"[scrape_pie] no category link named {CATEGORY_NAME!r} found")
        _log_diagnostics(page)
        return False

    print(f"[scrape_pie] opening category: {href}")
    page.goto(href, wait_until="networkidle", timeout=30000)
    _wait_for_app_ready(page)
    return True


def _extract_product_names(page) -> list[str]:
    titles = page.locator(PRODUCT_TITLE_SELECTOR)
    count = titles.count()
    if count == 0:
        # Legitimately empty category (e.g. menu not posted yet) looks the
        # same as a broken selector -- diagnostics tell them apart.
        _log_diagnostics(page)
        return []

    names = []
    for i in range(count):
        text = re.sub(r"\s+", " ", titles.nth(i).inner_text()).strip()
        if text and text.capitalize() not in names:
            names.append(text.capitalize())

    print(f"[scrape_pie] found {len(names)} product name(s): {names}")
    return names


def _log_diagnostics(page):
    """Nothing usable found -- log everything useful for figuring out why
    without needing another round-trip of "here's some HTML"."""
    print("[scrape_pie] diagnostics follow")
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
    try:
        link_texts = page.evaluate(
            """() => [...document.querySelectorAll('a[href*="/shop/"]')]
                .map(a => a.innerText.trim()).filter(Boolean).slice(0, 20)"""
        )
        print(f"[scrape_pie]   /shop/ link texts: {link_texts}")
    except Exception as e:
        print(f"[scrape_pie]   link text fetch failed: {e}")
    try:
        print(f"[scrape_pie]   count of {PRODUCT_TITLE_SELECTOR!r}: {page.locator(PRODUCT_TITLE_SELECTOR).count()}")
    except Exception as e:
        print(f"[scrape_pie]   selector count failed: {e}")


if __name__ == "__main__":
    for pie in scrape_pies():
        print(pie)
