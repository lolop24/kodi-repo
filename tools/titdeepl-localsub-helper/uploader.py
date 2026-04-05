#!/usr/bin/env python3
"""Browser-driven OpenSubtitles uploader used by the remote helper."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext, Error, Page, TimeoutError, sync_playwright

COM_UPLOAD_URL = "https://www.opensubtitles.com/en/upload"
ORG_UPLOAD_URL = "https://www.opensubtitles.org/en/upload"
DEFAULT_TIMEOUT_MS = 120_000
ROOT_DIR = Path(__file__).resolve().parent
STATE_DIR = ROOT_DIR / ".state"
DEFAULT_PROFILE_DIR = STATE_DIR / "browser-profile"
DEFAULT_SCREENSHOT_DIR = STATE_DIR / "screenshots"

LANGUAGE_ALIASES = {
    "ar": "Arabic",
    "bg": "Bulgarian",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "mk": "Macedonian",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "pt-br": "Portuguese (BR)",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sr": "Serbian",
    "sv": "Swedish",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
}


@dataclass
class UploadOptions:
    subtitle_files: list[Path]
    language: str
    imdb_id: str
    fps: str
    release_name: str
    movie_aka: str | None
    comment: str | None
    translator: str | None
    hearing_impaired: bool
    high_definition: bool
    machine_translated: bool
    foreign_parts_only: bool
    anonymous: bool
    username: str | None
    password: str | None
    remember_login: bool
    headless: bool
    submit: bool
    timeout_ms: int
    browser: str
    browser_path: Path | None
    user_data_dir: Path
    screenshot_dir: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fill the OpenSubtitles upload form in a real Chromium-based browser. "
            "By default it does not click final Upload; add --submit to do that."
        )
    )
    parser.add_argument(
        "--subtitle",
        dest="subtitle_files",
        nargs="+",
        required=True,
        help="One or more subtitle files to upload.",
    )
    parser.add_argument(
        "--language",
        required=True,
        help="Subtitle language label or short alias such as en, sk, cs, uk.",
    )
    parser.add_argument(
        "--imdb-id",
        required=True,
        help="IMDb title id, with or without tt prefix (example: tt0133093).",
    )
    parser.add_argument(
        "--fps",
        required=True,
        choices=["23.976", "23.980", "24.000", "25.000", "29.970", "30.000", "50.000", "59.940", "60.000"],
        help="Frame rate shown in the upload form.",
    )
    parser.add_argument("--release-name", required=True, help="Release name shown on the site.")
    parser.add_argument("--movie-aka", help="Optional alternate movie title.")
    parser.add_argument("--comment", help="Optional upload comment.")
    parser.add_argument("--translator", help="Optional translator credit.")
    parser.add_argument("--hearing-impaired", action="store_true", help="Mark subtitles for hearing impaired.")
    parser.add_argument("--high-definition", action="store_true", help="Mark subtitles for HD release.")
    parser.add_argument(
        "--machine-translated",
        action="store_true",
        help="Mark subtitles as machine translated.",
    )
    parser.add_argument("--foreign-parts-only", action="store_true", help="Mark subtitles as foreign parts only.")
    parser.add_argument(
        "--anonymous",
        action="store_true",
        help="Skip login and keep the upload anonymous.",
    )
    parser.add_argument("--username", help="OpenSubtitles username. Falls back to OPENSUBTITLES_USERNAME.")
    parser.add_argument("--password", help="OpenSubtitles password. Falls back to OPENSUBTITLES_PASSWORD.")
    parser.add_argument(
        "--no-remember-login",
        action="store_true",
        help="Do not tick 'remember me' on the legacy login form.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser headless. The site may block this mode.",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Actually click Upload after the form is filled.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_MS // 1000,
        help="Timeout in seconds for waits that depend on the website. Default: 120.",
    )
    parser.add_argument(
        "--browser",
        choices=["auto", "playwright", "chromium", "chrome", "msedge"],
        default="auto",
        help="Browser launch strategy. Default: auto.",
    )
    parser.add_argument(
        "--browser-path",
        help="Absolute path to a Chromium-based browser executable.",
    )
    parser.add_argument(
        "--user-data-dir",
        default=str(DEFAULT_PROFILE_DIR),
        help=f"Persistent browser profile directory. Default: {DEFAULT_PROFILE_DIR}",
    )
    parser.add_argument(
        "--screenshot-dir",
        default=str(DEFAULT_SCREENSHOT_DIR),
        help=f"Where failure screenshots are stored. Default: {DEFAULT_SCREENSHOT_DIR}",
    )
    return parser


def parse_args(argv: list[str]) -> UploadOptions:
    args = build_parser().parse_args(argv)

    subtitle_files = [Path(path).expanduser().resolve() for path in args.subtitle_files]
    missing = [str(path) for path in subtitle_files if not path.is_file()]
    if missing:
        raise SystemExit(f"Subtitle files not found: {', '.join(missing)}")

    username = args.username or os.getenv("OPENSUBTITLES_USERNAME")
    password = args.password or os.getenv("OPENSUBTITLES_PASSWORD")
    if not args.anonymous:
        if not username and sys.stdin.isatty():
            username = input("OpenSubtitles username: ").strip()
        if not password and sys.stdin.isatty():
            password = getpass.getpass("OpenSubtitles password: ")
        if not username or not password:
            raise SystemExit(
                "Login requested but credentials are missing. "
                "Use --anonymous, pass --username/--password, or set OPENSUBTITLES_USERNAME/OPENSUBTITLES_PASSWORD."
            )

    browser_path = None
    if args.browser_path:
        browser_path = Path(args.browser_path).expanduser().resolve()
        if not browser_path.is_file():
            raise SystemExit(f"Browser executable not found: {browser_path}")

    imdb_id = normalize_imdb_id(args.imdb_id)
    user_data_dir = Path(args.user_data_dir).expanduser().resolve()
    screenshot_dir = Path(args.screenshot_dir).expanduser().resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    return UploadOptions(
        subtitle_files=subtitle_files,
        language=args.language.strip(),
        imdb_id=imdb_id,
        fps=args.fps,
        release_name=args.release_name.strip(),
        movie_aka=clean_optional(args.movie_aka),
        comment=clean_optional(args.comment),
        translator=clean_optional(args.translator),
        hearing_impaired=args.hearing_impaired,
        high_definition=args.high_definition,
        machine_translated=args.machine_translated,
        foreign_parts_only=args.foreign_parts_only,
        anonymous=args.anonymous,
        username=username,
        password=password,
        remember_login=not args.no_remember_login,
        headless=args.headless,
        submit=args.submit,
        timeout_ms=args.timeout * 1000,
        browser=args.browser,
        browser_path=browser_path,
        user_data_dir=user_data_dir,
        screenshot_dir=screenshot_dir,
    )


def clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def normalize_imdb_id(value: str) -> str:
    stripped = value.strip()
    if stripped.lower().startswith("tt"):
        stripped = stripped[2:]
    if not stripped.isdigit():
        raise SystemExit(f"IMDb id must be digits or tt-prefixed digits, got: {value}")
    return stripped


def launch_browser_context(playwright, options: UploadOptions) -> BrowserContext:
    attempts = build_browser_attempts(options)
    last_error = None
    launch_args = browser_launch_args()
    for attempt in attempts:
        kwargs = {
            "user_data_dir": str(options.user_data_dir),
            "headless": options.headless,
            "viewport": {"width": 1600, "height": 1200},
            "args": launch_args,
        }
        if attempt.get("channel"):
            kwargs["channel"] = attempt["channel"]
        if attempt.get("executable_path"):
            kwargs["executable_path"] = attempt["executable_path"]

        try:
            print(f"Launching browser via {attempt['label']}...")
            return playwright.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:  # pragma: no cover - depends on runtime browser availability
            last_error = exc
            print(f"Launch failed for {attempt['label']}: {exc}", file=sys.stderr)

    raise RuntimeError(f"Could not launch a supported browser. Last error: {last_error}")


def browser_launch_args() -> list[str]:
    args = ["--disable-blink-features=AutomationControlled"]
    geteuid = getattr(os, "geteuid", None)
    if callable(geteuid):
        try:
            if geteuid() == 0:
                args.append("--no-sandbox")
        except Exception:
            pass
    return args


def build_browser_attempts(options: UploadOptions) -> list[dict[str, str]]:
    attempts = []
    if options.browser_path is not None:
        attempts.append(
            {
                "label": str(options.browser_path),
                "executable_path": str(options.browser_path),
            }
        )
        if options.browser != "auto":
            return attempts

    if options.browser == "auto":
        if sys.platform.startswith("win"):
            order = ["msedge", "chrome", "playwright"]
        else:
            order = ["playwright", "chrome", "msedge"]
    elif options.browser == "chromium":
        order = ["playwright"]
    else:
        order = [options.browser]

    for name in order:
        if name == "playwright":
            attempts.append({"label": "Playwright Chromium"})
        elif name == "chrome":
            attempts.append({"label": "Google Chrome channel", "channel": "chrome"})
        elif name == "msedge":
            attempts.append({"label": "Microsoft Edge channel", "channel": "msedge"})

    return attempts


def main(argv: list[str]) -> int:
    options = parse_args(argv)

    with sync_playwright() as playwright:
        context = launch_browser_context(playwright, options)
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(options.timeout_ms)

        try:
            open_real_upload_page(page, options)
            if not options.anonymous:
                ensure_logged_in(page, options)
                open_real_upload_page(page, options)
            fill_upload_form(page, options)

            if not options.submit:
                print("Form is filled. Review it in the opened browser window.")
                pause_if_interactive("Press Enter to close the browser without submitting.")
                return 0

            print("Submitting upload...")
            page.locator("input#submit").click()
            wait_for_submit_result(page, options.timeout_ms)
            print(f"Submission finished. Current URL: {page.url}")
            print(page_title_summary(page))
            pause_if_interactive("Press Enter to close the browser.")
            return 0
        except Exception as exc:
            save_failure_artifacts(page, options.screenshot_dir)
            print(f"Upload failed: {exc}", file=sys.stderr)
            print(
                "A screenshot was saved in "
                f"{options.screenshot_dir}. Re-run without --headless if the site blocks automation.",
                file=sys.stderr,
            )
            return 1
        finally:
            context.close()


def open_real_upload_page(page: Page, options: UploadOptions) -> None:
    print("Opening OpenSubtitles upload page...")
    page.goto(COM_UPLOAD_URL, wait_until="domcontentloaded")

    deadline = time.monotonic() + (options.timeout_ms / 1000)
    opened_org = False
    while time.monotonic() < deadline:
        body = safe_body_text(page)
        title = safe_title(page)

        if "During our beta phase, uploads are not yet possible" in body:
            if not opened_org:
                print("Direct .com uploads are disabled, switching to the legacy .org form...")
                page.goto(ORG_UPLOAD_URL, wait_until="domcontentloaded")
                opened_org = True
            page.wait_for_timeout(1000)
            continue

        if "Upload subtitles" in body and "Movie Information" in body and "403 Forbidden" not in body:
            print(f"Upload form ready at {page.url}")
            return

        if "403 Forbidden" in body and options.headless:
            raise RuntimeError("OpenSubtitles blocked the headless session. Re-run without --headless.")

        if "Making sure you're not a bot!" in body or title == "Making sure you're not a bot!":
            print("Waiting for the anti-bot challenge to finish...")
        page.wait_for_timeout(1000)

    raise RuntimeError(f"Timed out waiting for upload form at {page.url}")


def ensure_logged_in(page: Page, options: UploadOptions) -> None:
    if not needs_login(page):
        print("A logged-in session is already available in the browser profile.")
        return

    print("Logging in on the legacy upload page...")
    login_form = page.locator("form[action*='/login/']").first
    if login_form.count() == 0:
        raise RuntimeError("Could not find the login form on the upload page.")

    login_form.locator("input[name='user']").fill(options.username or "")
    login_form.locator("input[name='password']").fill(options.password or "")

    remember_box = login_form.locator("input[name='remember']")
    if options.remember_login:
        remember_box.check()
    else:
        remember_box.uncheck()

    if login_form.locator("iframe[src*='recaptcha']").count() > 0:
        print("A CAPTCHA may be shown in the browser. Solve it there if needed.")

    login_form.locator("input[type='submit'][value='Login']").click()
    wait_for_login_result(page, options.timeout_ms)


def wait_for_login_result(page: Page, timeout_ms: int) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        if not needs_login(page):
            print("Login appears to be successful.")
            return

        body = safe_body_text(page)
        if "403 Forbidden" in body:
            raise RuntimeError("The site blocked login after submission.")
        page.wait_for_timeout(1000)

    raise RuntimeError(
        "Login did not finish in time. The site may be asking for CAPTCHA/manual confirmation, "
        "or the credentials may not be valid on opensubtitles.org."
    )


def needs_login(page: Page) -> bool:
    body = safe_body_text(page)
    if "You are not logged in!" in body:
        return True
    return page.locator("form[action*='/login/']").count() > 0


def fill_upload_form(page: Page, options: UploadOptions) -> None:
    print("Filling upload form...")
    language_value, language_label = resolve_language_option(page, options.language)
    page.locator("select[name='sublanguageid']").select_option(language_value)
    page.locator("input[name='IDMovieImdb']").fill(options.imdb_id)
    page.locator("select[name='MovieFPS']").select_option(label=options.fps)
    page.locator("input[name='MovieReleaseName']").fill(options.release_name)

    if options.movie_aka is not None:
        page.locator("input[name='MovieAka']").fill(options.movie_aka)
    if options.comment is not None:
        page.locator("textarea[name='SubAutorComment']").fill(options.comment)
    if options.translator is not None:
        page.locator("input[name='SubTranslator']").fill(options.translator)

    set_checkbox(page, "input[name='HearingImpaired']", options.hearing_impaired)
    set_checkbox(page, "input[name='HighDefinition']", options.high_definition)
    set_checkbox(page, "input[name='AutoTranslation']", options.machine_translated)
    set_checkbox(page, "input[name='ForeignPartsOnly']", options.foreign_parts_only)

    page.locator("input[name='subs[]']").set_input_files([str(path) for path in options.subtitle_files])
    print(
        "Prepared upload: "
        f"{language_label}, IMDb {options.imdb_id}, FPS {options.fps}, "
        f"{len(options.subtitle_files)} file(s)."
    )


def resolve_language_option(page: Page, requested: str) -> tuple[str, str]:
    normalized = requested.strip()
    alias = LANGUAGE_ALIASES.get(normalized.lower(), normalized)
    options = page.locator("select[name='sublanguageid'] option").evaluate_all(
        "items => items.map(item => ({value: item.value, label: (item.textContent || '').trim()}))"
    )

    for candidate in (normalized, alias):
        for option in options:
            if not option["value"]:
                continue
            if option["label"].lower() == candidate.lower():
                return option["value"], option["label"]
            if option["value"].lower() == candidate.lower():
                return option["value"], option["label"]

    sample = ", ".join(option["label"] for option in options if option["value"])[:300]
    raise RuntimeError(f"Language '{requested}' was not found in the upload form. Sample options: {sample}")


def set_checkbox(page: Page, selector: str, enabled: bool) -> None:
    checkbox = page.locator(selector)
    if enabled:
        checkbox.check()
    else:
        checkbox.uncheck()


def wait_for_submit_result(page: Page, timeout_ms: int) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        body = safe_body_text(page)
        if "403 Forbidden" in body:
            raise RuntimeError("The site blocked the submission.")
        if "Upload subtitles" not in body or "Movie Information" not in body:
            return
        page.wait_for_timeout(1000)

    raise RuntimeError("Submission did not leave the upload form before timeout.")


def safe_body_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5_000)
    except (Error, TimeoutError):
        return ""


def safe_title(page: Page) -> str:
    try:
        return page.title()
    except Error:
        return ""


def page_title_summary(page: Page) -> str:
    title = safe_title(page)
    body = safe_body_text(page).strip().replace("\n", " ")
    return f"Title: {title or '(none)'} | Body excerpt: {body[:250]}"


def save_failure_artifacts(page: Page, screenshot_dir: Path) -> None:
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    png_path = screenshot_dir / f"failure-{stamp}.png"
    html_path = screenshot_dir / f"failure-{stamp}.html"
    try:
        page.screenshot(path=str(png_path), full_page=True)
    except Error:
        pass
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Error:
        pass


def pause_if_interactive(message: str) -> None:
    if sys.stdin.isatty():
        try:
            input(message)
        except EOFError:
            return
        return
    print(message)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
