"""Build an Apple Silicon .app with its own Python runtime; never package credentials/data."""

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
APP = BUILD / "AI Billing Monitor.app"


def run(arguments, cwd=ROOT):
    subprocess.run(arguments, cwd=cwd, check=True)


def main():
    BUILD.mkdir(exist_ok=True)
    run(["swift", "build", "-c", "release"], ROOT / "macOS")
    run(
        [
            "uv",
            "run",
            "--with",
            "pyinstaller",
            "pyinstaller",
            "--noconfirm",
            "--clean",
            "--name",
            "monitor-backend",
            "--onedir",
            "--distpath",
            str(BUILD / "python"),
            "--workpath",
            str(BUILD / "pyinstaller-work"),
            "--specpath",
            str(BUILD),
            "--collect-submodules",
            "uvicorn",
            "--collect-submodules",
            "app",
            "--exclude-module",
            "pytest",
            "--exclude-module",
            "tkinter",
            "desktop_entry.py",
        ],
        ROOT / "backend",
    )
    # Only this generated build artifact is replaced. Source and user data are outside it.
    if APP.exists():
        shutil.rmtree(APP)
    contents = APP / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Resources").mkdir()
    shutil.copy2(
        ROOT / "macOS/.build/release/AIBillingMonitor", contents / "MacOS/AIBillingMonitor"
    )
    shutil.copytree(BUILD / "python/monitor-backend", contents / "Resources/backend")
    run(["swift", str(ROOT / "scripts/make_icon.swift"), str(BUILD / "icon.png")])
    iconset = BUILD / "AppIcon.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            run(
                [
                    "sips",
                    "-z",
                    str(size * scale),
                    str(size * scale),
                    str(BUILD / "icon.png"),
                    "--out",
                    str(iconset / f"icon_{size}x{size}{suffix}.png"),
                ]
            )
    run(["iconutil", "-c", "icns", str(iconset), "-o", str(contents / "Resources/AppIcon.icns")])
    info = {
        "CFBundleIdentifier": "au.david.aibillingmonitor",
        "CFBundleName": "AI Billing Monitor",
        "CFBundleDisplayName": "AI Billing Monitor",
        "CFBundleExecutable": "AIBillingMonitor",
        "CFBundleIconFile": "AppIcon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "0.8.1",
        "CFBundleVersion": "17",
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        # Import hint is a path only. No keys or database files are bundled.
        "MonitorImportDirectory": str(ROOT / "backend"),
    }
    with (contents / "Info.plist").open("wb") as output:
        plistlib.dump(info, output)
    run(["codesign", "--force", "--deep", "--sign", "-", str(APP)])
    os.chmod(contents / "MacOS/AIBillingMonitor", 0o755)
    print(APP)


if __name__ == "__main__":
    main()
