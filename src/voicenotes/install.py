"""Build and install Voicenotes.app, and manage starting at login.

The bundle is small: an embedded-Python launcher, an icon and config. The Python code and
packages stay in the project folder, so code changes need no rebuild (and macOS keeps the
granted permissions, which are tied to the app's signature).
"""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

BUNDLE_ID = "nl.voicenotes.app"
APP_NAME = "Voicenotes"
DEFAULT_DEST = Path.home() / "Applications"
AGENT = Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"
PROJECT = Path(__file__).resolve().parents[2]
MACOS_SRC = PROJECT / "macos"

INFO = {
    "CFBundleIdentifier": BUNDLE_ID,
    "CFBundleName": APP_NAME,
    "CFBundleDisplayName": APP_NAME,
    "CFBundleExecutable": APP_NAME,
    "CFBundleIconFile": "AppIcon",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": "0.1.0",
    "CFBundleVersion": "1",
    "LSMinimumSystemVersion": "14.2",
    "LSUIElement": True,               # menu bar app; the Dock icon appears with the window
    "NSHighResolutionCapable": True,
    "NSMicrophoneUsageDescription":
        "Voicenotes neemt je microfoon op tijdens meetings en wanneer je met fn dicteert.",
    "NSAudioCaptureUsageDescription":
        "Voicenotes neemt het geluid van je Mac op (bijvoorbeeld Teams of WhatsApp) om meetings "
        "te transcriberen. Alles blijft op deze Mac.",
}


def app_path(dest: Path = DEFAULT_DEST) -> Path:
    return dest / f"{APP_NAME}.app"


def _launcher_config() -> dict:
    from .audio.system_tap import find_audiotap
    site_packages = sysconfig.get_path("purelib")  # the project's (virtual env) packages
    return {
        "python_home": sys.base_prefix,
        "libpython": str(Path(sysconfig.get_config_var("LIBDIR"))
                         / sysconfig.get_config_var("LDLIBRARY")),
        "site_packages": str(Path(site_packages).resolve()),
        "src": str(PROJECT / "src"),
        "audiotap": str(find_audiotap()),
        "data_dir": os.environ.get("VOICENOTES_HOME", ""),
    }


def make_icon(out: Path) -> None:
    """Draw the app icon (white microphone on Delft blue) and convert it to .icns."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient, QPainter,
                               QPainterPath, QPen)
    _app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841

    def draw(size: int) -> QImage:
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = size / 1024
        # macOS icon grid: 824 px rounded square centred in 1024
        rect = QRectF(100 * s, 100 * s, 824 * s, 824 * s)
        grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        grad.setColorAt(0, QColor("#3A62C4"))
        grad.setColorAt(1, QColor("#22408A"))
        path = QPainterPath()
        path.addRoundedRect(rect, 185 * s, 185 * s)
        p.fillPath(path, grad)
        white = QColor("#FFFFFF")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(white)
        p.drawRoundedRect(QRectF(422 * s, 250 * s, 180 * s, 330 * s), 90 * s, 90 * s)
        pen = QPen(white, 46 * s)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        arc = QPainterPath(QPointF(332 * s, 470 * s))
        arc.cubicTo(QPointF(332 * s, 720 * s), QPointF(692 * s, 720 * s), QPointF(692 * s, 470 * s))
        p.drawPath(arc)
        p.drawLine(QPointF(512 * s, 668 * s), QPointF(512 * s, 770 * s))
        # recording dot
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#F2555A"))
        p.drawEllipse(QRectF(640 * s, 200 * s, 150 * s, 150 * s))
        p.end()
        return img

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            draw(base).save(str(iconset / f"icon_{base}x{base}.png"))
            draw(base * 2).save(str(iconset / f"icon_{base}x{base}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)


def build(dest: Path = DEFAULT_DEST) -> Path:
    app = app_path(dest)
    contents = app / "Contents"
    tmp = Path(tempfile.mkdtemp())
    staged = tmp / f"{APP_NAME}.app" / "Contents"
    (staged / "MacOS").mkdir(parents=True)
    (staged / "Resources" / "boot").mkdir(parents=True)

    subprocess.run(["swiftc", "-O", str(MACOS_SRC / "launcher.swift"),
                    "-o", str(staged / "MacOS" / APP_NAME)], check=True)
    shutil.copy2(MACOS_SRC / "sitecustomize.py", staged / "Resources" / "boot" / "sitecustomize.py")
    (staged / "Resources" / "launcher.json").write_text(json.dumps(_launcher_config(), indent=2))
    make_icon(staged / "Resources" / "AppIcon.icns")
    with open(staged / "Info.plist", "wb") as f:
        plistlib.dump(INFO, f)

    # Ad-hoc signature: macOS ties the privacy permissions to it.
    subprocess.run(["codesign", "--force", "--sign", "-", "--identifier", BUNDLE_ID,
                    str(staged.parent)], check=True, capture_output=True)

    dest.mkdir(parents=True, exist_ok=True)
    if app.exists():
        shutil.rmtree(app)
    shutil.move(str(staged.parent), str(app))
    shutil.rmtree(tmp, ignore_errors=True)
    # Make Finder/Spotlight/Launchpad pick it up.
    subprocess.run(["/System/Library/Frameworks/CoreServices.framework/Frameworks/"
                    "LaunchServices.framework/Support/lsregister", "-f", str(app)],
                   capture_output=True)
    return app


def login_enabled() -> bool:
    return AGENT.exists()


def set_login(enabled: bool, dest: Path | None = None) -> None:
    """Start Voicenotes (menu bar only) when you log in, via a LaunchAgent."""
    if not enabled:
        AGENT.unlink(missing_ok=True)
        return
    app = app_path(dest) if dest else _installed_app()
    if app is None:
        raise FileNotFoundError("Voicenotes.app is nog niet geïnstalleerd")
    AGENT.parent.mkdir(parents=True, exist_ok=True)
    with open(AGENT, "wb") as f:
        plistlib.dump({
            "Label": BUNDLE_ID,
            # `open` launches it like Finder does; -g keeps your current app in front.
            "ProgramArguments": ["/usr/bin/open", "-g", "-a", str(app), "--args", "--hidden"],
            "RunAtLoad": True,
            "LimitLoadToSessionType": "Aqua",
        }, f)


def _installed_app() -> Path | None:
    for d in (DEFAULT_DEST, Path("/Applications")):
        if app_path(d).exists():
            return app_path(d)
    return None


def running_in_bundle() -> bool:
    return f"{APP_NAME}.app/Contents/MacOS" in sys.executable
