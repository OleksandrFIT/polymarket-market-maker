#!/usr/bin/env python3
"""
Auto-claim winning Polymarket positions via Playwright browser automation.

Periodically checks for resolved markets with unclaimed winnings and claims them.

Usage:
    python auto_claim.py                    # Run once
    python auto_claim.py --loop --interval 300  # Check every 5 minutes

Requires:
    pip install playwright
    playwright install chromium
"""

import argparse
import asyncio
import os
import sys
import time

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Install playwright: pip install playwright && playwright install chromium")
    sys.exit(1)


POLYMARKET_URL = "https://polymarket.com"
PORTFOLIO_URL = f"{POLYMARKET_URL}/portfolio"


async def claim_winnings(headless: bool = True, timeout_ms: int = 30000) -> int:
    """
    Open Polymarket portfolio, find claimable positions, click claim.

    Returns number of positions claimed.
    """
    claimed = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)

        # Use persistent context to reuse login state
        storage_path = os.path.expanduser("~/.polymarket_auth.json")
        context = await browser.new_context(
            storage_state=storage_path if os.path.exists(storage_path) else None,
        )
        page = await context.new_page()

        try:
            print(f"Navigating to {PORTFOLIO_URL}...")
            await page.goto(PORTFOLIO_URL, wait_until="networkidle", timeout=timeout_ms)
            await asyncio.sleep(3)

            # Check if logged in
            if "sign-in" in page.url.lower() or "login" in page.url.lower():
                print("Not logged in. Please log in manually first.")
                print("Run with --no-headless to log in, then re-run.")
                await browser.close()
                return 0

            # Save auth state for future runs
            await context.storage_state(path=storage_path)

            # Find claim buttons
            claim_buttons = await page.query_selector_all(
                'button:has-text("Claim"), button:has-text("Redeem"), '
                '[data-testid="claim-button"], [data-testid="redeem-button"]'
            )

            if not claim_buttons:
                # Try alternative selectors
                claim_buttons = await page.query_selector_all(
                    'button:has-text("claim"), button:has-text("redeem")'
                )

            if not claim_buttons:
                print("No claimable positions found.")
                await browser.close()
                return 0

            print(f"Found {len(claim_buttons)} claimable position(s)")

            for i, btn in enumerate(claim_buttons):
                try:
                    text = await btn.text_content()
                    print(f"  Claiming {i + 1}/{len(claim_buttons)}: {text}")
                    await btn.click()
                    await asyncio.sleep(3)

                    # Wait for confirmation dialog if any
                    confirm = await page.query_selector(
                        'button:has-text("Confirm"), button:has-text("Yes")'
                    )
                    if confirm:
                        await confirm.click()
                        await asyncio.sleep(5)

                    claimed += 1
                    print(f"    Claimed successfully")

                except Exception as e:
                    print(f"    Failed to claim: {e}")

        except Exception as e:
            print(f"Error during claim process: {e}")
        finally:
            await browser.close()

    return claimed


async def run_loop(interval: int, headless: bool):
    """Continuously check and claim winnings."""
    print(f"Auto-claim loop started (checking every {interval}s)")
    while True:
        try:
            n = await claim_winnings(headless=headless)
            ts = time.strftime("%H:%M:%S")
            if n > 0:
                print(f"[{ts}] Claimed {n} position(s)")
            else:
                print(f"[{ts}] No claims needed")
        except Exception as e:
            print(f"Error in claim loop: {e}")

        await asyncio.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Auto-claim Polymarket winnings")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=300, help="Check interval in seconds (default 300)")
    parser.add_argument("--no-headless", action="store_true", help="Show browser window")
    args = parser.parse_args()

    headless = not args.no_headless

    if args.loop:
        asyncio.run(run_loop(args.interval, headless))
    else:
        n = asyncio.run(claim_winnings(headless=headless))
        print(f"\nDone. Claimed {n} position(s).")


if __name__ == "__main__":
    main()
