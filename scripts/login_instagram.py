from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    root = Path(os.getenv("PROJECT_ROOT", Path.cwd())).resolve()
    profile_dir = Path(os.getenv("PROFILE_DIR", root / "profile")).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport={"width": 1365, "height": 900},
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        page = context.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded")
        print("Browser opened. Log in to Instagram manually.")
        print("After the Instagram home page is visible, return here and press Enter.")
        input()
        context.close()
        print(f"Saved browser profile at {profile_dir}")


if __name__ == "__main__":
    main()
