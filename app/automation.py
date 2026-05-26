from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import settings


class RecoverableAutomationError(Exception):
    pass


class TerminalAutomationError(Exception):
    pass


@dataclass
class AutomationResult:
    ok: bool
    result: dict[str, Any]


def _final_caption(post: dict[str, Any]) -> str:
    caption = (post.get("caption") or "").strip()
    hashtags = post.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtag_items = [item.strip() for item in hashtags.replace(",", " ").split() if item.strip()]
    else:
        hashtag_items = [str(item).strip() for item in hashtags if str(item).strip()]
    existing_hashtags = {match.group(0).lower() for match in re.finditer(r"#\S+", caption)}
    missing_hashtags = [item for item in hashtag_items if item.lower() not in existing_hashtags]
    hashtag_text = " ".join(missing_hashtags)
    return f"{caption}\n\n{hashtag_text}".strip()


def _click_first(page: Any, candidates: list[Any], timeout: int = 6000) -> None:
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            locator = candidate() if callable(candidate) else candidate
            locator.click(timeout=timeout)
            return
        except Exception as error:  # Playwright raises its own Error type.
            last_error = error
    raise RecoverableAutomationError(str(last_error or "No selector matched"))


def _select_original_crop(page: Any) -> str:
    """Keep portrait artwork uncropped in Instagram's composer."""
    crop_button = page.locator('svg[aria-label="Select crop"]').locator(
        'xpath=ancestor::*[@role="button" or self::button][1]'
    ).first
    if crop_button.count() == 0:
        crop_button = page.locator('svg[aria-label="Pilih potongan"]').locator(
            'xpath=ancestor::*[@role="button" or self::button][1]'
        ).first
    if crop_button.count() == 0:
        raise RecoverableAutomationError("Instagram crop control not found")

    crop_button.click(timeout=10000)
    page.wait_for_timeout(1200)

    for label in ["Original", "Asli", "4:5"]:
        option = page.locator('div[role="button"]').filter(has_text=label).first
        if option.count() > 0:
            option.click(timeout=8000)
            page.wait_for_timeout(500)
            return label

    raise RecoverableAutomationError("Instagram portrait crop option not found")


def _detect_post_share_result(page: Any) -> str:
    success_text = page.get_by_text(
        re.compile("Your post has been shared|Postingan Anda telah dibagikan|shared", re.I)
    ).first
    try:
        success_text.wait_for(timeout=settings.upload_timeout_seconds * 1000)
        return "instagram_web_success_ui"
    except Exception:
        pass

    body_text = page.locator("body").inner_text(timeout=5000)
    if re.search(
        r"something went wrong|try again|couldn'?t share|could not share|gagal|terjadi kesalahan|coba lagi",
        body_text,
        re.I,
    ):
        raise RecoverableAutomationError("Instagram showed an error after Share")

    share_buttons = page.get_by_role("button", name=re.compile("^(Share|Bagikan)$", re.I)).count()
    if share_buttons == 0:
        return "instagram_web_share_clicked_no_error"

    # Instagram sometimes publishes successfully but never renders the success text in headless Chrome.
    # Avoid retrying and duplicating a post when Share was clicked and no explicit failure appeared.
    return "instagram_web_share_clicked_no_confirmation"


def _latest_profile_post_url(page: Any) -> str | None:
    username = settings.instagram_username.strip().lstrip("@")
    if not username:
        return None
    page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    post_links = page.locator('a[href^="/p/"], a[href^="/reel/"], a[href^="/tv/"]')
    if post_links.count() == 0:
        return None
    href = post_links.first.get_attribute("href", timeout=5000)
    if not href:
        return None
    if href.startswith("http"):
        return href
    return f"https://www.instagram.com{href}"


def publish_to_instagram(post: dict[str, Any]) -> AutomationResult:
    media_files = [str(path) for path in post.get("media_files") or []]
    missing = [path for path in media_files if not Path(path).exists()]
    if missing:
        raise TerminalAutomationError(f"Missing media files: {', '.join(missing)}")

    if settings.instagram_dry_run:
        return AutomationResult(
            ok=True,
            result={"confirmed_by": "dry_run", "media_count": len(media_files), "url": None},
        )

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as error:
        raise TerminalAutomationError(f"Playwright is not installed: {error}") from error

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.profile_dir),
            headless=settings.playwright_headless,
            viewport={"width": 1365, "height": 900},
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        page = context.new_page()
        try:
            page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            if "accounts/login" in page.url or page.get_by_text(re.compile("Log in|Masuk", re.I)).count() > 0:
                raise TerminalAutomationError("Instagram session expired or login checkpoint required")

            _click_first(
                page,
                [
                    lambda: page.locator('a:has(svg[aria-label="New post"])').first,
                    lambda: page.locator('a:has(svg[aria-label="Create"])').first,
                    lambda: page.get_by_role("link", name=re.compile("Create|Buat|New post", re.I)).first,
                    lambda: page.get_by_role("button", name=re.compile("Create|Buat|New post", re.I)).first,
                ],
            )
            page.wait_for_timeout(1000)
            post_menu_items = page.locator('a:has-text("Post")')
            if post_menu_items.count() > 1:
                post_menu_items.nth(1).click(timeout=10000)
            else:
                post_menu_items.first.click(timeout=10000)
            page.wait_for_timeout(3000)

            file_input = page.locator('input[type="file"]').first
            if file_input.count() > 0:
                file_input.set_input_files(media_files)
            else:
                try:
                    with page.expect_file_chooser(timeout=10000) as chooser_info:
                        _click_first(
                            page,
                            [
                                lambda: page.get_by_text(re.compile("Select from computer|Pilih dari komputer", re.I)).first,
                            ],
                        )
                    chooser_info.value.set_files(media_files)
                except PlaywrightTimeoutError:
                    file_input = page.locator('input[type="file"]').first
                    if file_input.count() == 0:
                        raise RecoverableAutomationError("Instagram upload file input not found")
                    file_input.set_input_files(media_files)

            page.wait_for_timeout(1500)
            selected_crop = _select_original_crop(page)

            for _ in range(2):
                _click_first(
                    page,
                    [
                        lambda: page.get_by_role("button", name=re.compile("Next|Selanjutnya", re.I)).first,
                        lambda: page.get_by_text(re.compile("Next|Selanjutnya", re.I)).first,
                    ],
                    timeout=15000,
                )
                page.wait_for_timeout(1500)

            text = _final_caption(post)
            caption_box = page.locator('[contenteditable="true"][aria-label="Write a caption..."]').first
            if caption_box.count() == 0:
                caption_box = page.get_by_role("textbox", name=re.compile("Write a caption|Tulis keterangan", re.I)).first
            caption_box.fill(text, timeout=10000)
            page.wait_for_timeout(500)
            if text and text.strip() not in caption_box.inner_text(timeout=5000).strip():
                raise RecoverableAutomationError("Instagram caption field did not retain text")

            _click_first(
                page,
                [
                    lambda: page.get_by_role("button", name=re.compile("^(Share|Bagikan)$", re.I)).first,
                    lambda: page.locator('div[role="button"]').filter(has_text=re.compile("^(Share|Bagikan)$", re.I)).first,
                ],
                timeout=15000,
            )

            page.wait_for_timeout(5000)
            confirmed_by = _detect_post_share_result(page)
            try:
                post_url = _latest_profile_post_url(page)
            except Exception:
                post_url = None
            return AutomationResult(
                ok=True,
                result={
                    "confirmed_by": confirmed_by,
                    "url": post_url,
                    "caption_length": len(text),
                    "selected_crop": selected_crop,
                },
            )
        except TerminalAutomationError:
            raise
        except RecoverableAutomationError:
            raise
        except Exception as error:
            raise RecoverableAutomationError(str(error)) from error
        finally:
            context.close()
