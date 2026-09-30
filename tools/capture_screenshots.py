#!/usr/bin/env python3
"""Regenerate the README screenshots from the synthetic demo database.

Writes cropped dashboard images to docs/images. Use tools/make_demo_db.py to
create repeatable synthetic data before capturing them.

    python3 tools/make_demo_db.py --out demo.db
    python3 tools/capture_screenshots.py --db demo.db

Requires Google Chrome (headless) and Pillow:

    pip install '.[docs]'

This script does not contact Google Cloud.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store  # noqa: E402
from dashboard import render_dashboard  # noqa: E402

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
    "chromium-browser",
]

# Where the page is split into separate images, in device pixels at 2x scale.
# These land inside the blank gutters between cards; adjust if the dashboard
# layout changes materially.
DEFAULT_SPLITS = (2196, 4474)

VIEWPORT_WIDTH = 1440
CAPTURE_HEIGHT = 4600
SCALE = 2


def find_chrome(explicit: str | None) -> str:
    for candidate in [explicit] if explicit else CHROME_CANDIDATES:
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return candidate
    raise SystemExit(
        "Chrome not found. Pass --chrome /path/to/chrome, or install Google Chrome."
    )


def shoot(chrome: str, html: Path, png: Path) -> None:
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            f"--force-device-scale-factor={SCALE}",
            f"--window-size={VIEWPORT_WIDTH},{CAPTURE_HEIGHT}",
            "--virtual-time-budget=6000",
            f"--screenshot={png}",
            html.resolve().as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    if not png.exists():
        raise SystemExit(f"Chrome produced no output for {html}")


def content_bottom(image, pad: int = 24) -> int:
    """Last row containing anything darker than the card backgrounds."""
    width, height = image.size
    grey = image.convert("L")
    pixels = grey.load()
    for y in range(height - 1, -1, -1):
        if min(pixels[x, y] for x in range(0, width, 7)) < 200:
            return min(height, y + pad)
    return height


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default="demo.db", help="database to render (default: demo.db)")
    p.add_argument("--out-dir", default="docs/images")
    p.add_argument("--project", default="example-project")
    p.add_argument("--chrome", help="path to the Chrome or Chromium binary")
    p.add_argument(
        "--splits",
        type=int,
        nargs=2,
        default=list(DEFAULT_SPLITS),
        metavar=("ROW", "ROW"),
        help=f"rows at which to divide the light capture (default: {DEFAULT_SPLITS})",
    )
    args = p.parse_args()

    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("Pillow is required: pip install '.[docs]'") from None

    if not Path(args.db).exists():
        raise SystemExit(f"{args.db} not found. Run tools/make_demo_db.py first.")

    chrome = find_chrome(args.chrome)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = store.connect(args.db)
    written = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for theme in ("light", "dark"):
            html = tmp_path / f"{theme}.html"
            html.write_text(
                render_dashboard(
                    conn,
                    project=args.project,
                    theme=theme,
                    generated_at="2026-07-24 18:00 UTC",
                ),
                encoding="utf-8",
            )
            raw = tmp_path / f"{theme}.png"
            shoot(chrome, html, raw)

            image = Image.open(raw).convert("RGB")
            bottom = content_bottom(image)

            if theme == "light":
                first, second = args.splits
                sections = [
                    ("dashboard-overview", 0, first),
                    ("dashboard-breakdowns", first, second),
                    ("dashboard-tables", second, bottom),
                ]
            else:
                sections = [("dashboard-dark", 0, args.splits[0])]

            for name, top, end in sections:
                target = out_dir / f"{name}.png"
                image.crop((0, top, image.width, end)).save(target, optimize=True)
                written.append((target, end - top))

    conn.close()

    for target, height in written:
        print(f"wrote {target}  ({image.width}x{height}, {target.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
