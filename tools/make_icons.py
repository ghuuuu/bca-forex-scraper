"""Generate the PWA icon PNGs from BCA's mark. Run from the project root:

    python tools/make_icons.py

iOS will not accept SVG for a home-screen icon, so these have to be real PNGs.
The artwork is `app_icon_svg()` in bca_forex/chart.py, built from
bca_forex/assets/BCA-Icon.svg — the same SVG the page uses as its favicon, so
the tab and the home screen cannot drift apart.

Rasterising SVG paths needs a real renderer and the project has no imaging
library, so this one development step borrows headless Chrome (or Edge). The
PNGs are committed: nothing at run time, and nothing in the Docker image, needs
a browser. Set CHROME=/path/to/chrome if it is not found.

After changing the icons, bump CACHE in chart.SERVICE_WORKER, or installed
apps keep the old ones.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bca_forex.chart import ASSETS, app_icon_svg  # noqa: E402

BROWSERS = (
    os.environ.get("CHROME"), "chrome", "google-chrome", "chromium", "msedge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

#: Headless Chrome will not open a window much narrower than 500 px, so every
#: icon is laid out at this size and scaled to its real one by the pixel ratio.
LAYOUT = 512

ICONS = [
    # name,                    size, corner, content
    ("icon-192.png",            192,  0.22,   0.64),
    ("icon-512.png",            512,  0.22,   0.64),
    ("icon-512-maskable.png",   512,  0.00,   0.54),  # safe zone is the inner 80% circle
    ("apple-touch-icon.png",    180,  0.00,   0.64),  # iOS applies its own mask
]


def find_browser() -> str:
    for b in BROWSERS:
        if b and (shutil.which(b) or Path(b).exists()):
            return shutil.which(b) or b
    sys.exit("no Chrome or Edge found; set CHROME=/path/to/chrome")


def render(browser: str, svg: str, size: int, out: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "icon.html"
        page.write_text(
            "<!doctype html><style>html,body{margin:0;background:transparent}"
            f"svg{{display:block;width:{LAYOUT}px;height:{LAYOUT}px}}</style>{svg}",
            encoding="utf-8",
        )
        subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             f"--window-size={LAYOUT},{LAYOUT}", f"--force-device-scale-factor={size / LAYOUT}",
             "--default-background-color=00000000",   # rounded corners stay transparent
             f"--user-data-dir={Path(tmp) / 'profile'}", f"--screenshot={out}", page.as_uri()],
            check=True, capture_output=True, timeout=120,
        )
    got = int.from_bytes(out.read_bytes()[16:20], "big")
    if got != size:
        sys.exit(f"{out.name}: rendered {got}px wide, wanted {size}px")


def main() -> None:
    browser = find_browser()
    for name, size, corner, content in ICONS:
        out = ASSETS / name
        render(browser, app_icon_svg(corner, content), size, out)
        print(f"{name:<26} {size}x{size}  {out.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
