"""
py2app setup for Nova .app bundle.

Usage:
    python setup_app.py py2app

Or use the build script:
    bash scripts/build-app.sh
"""

import os
import sys
from setuptools import setup

# Ensure we're in the project root
os.chdir(os.path.dirname(os.path.abspath(__file__)))

APP = ["launcher.py"]
APP_NAME = "Nova"

# Data files to include in the bundle
DATA_FILES = [
    ("", ["config.py"]),
]

# Include the src package
PACKAGES = ["src"]

OPTIONS = {
    "argv_emulation": False,  # Not needed, we handle args ourselves
    "plist": {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": "com.escipion.nova",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleIconFile": "AppIcon",
        "LSMinimumSystemVersion": "12.0",
        "LSUIElement": False,  # Show in Dock
        "NSMicrophoneUsageDescription": "Nova needs microphone access for voice commands.",
        "NSCameraUsageDescription": "Nova can use the camera for vision features.",
        "NSAppleEventsUsageDescription": "Nova needs automation access for screen capture.",
    },
    "packages": PACKAGES,
    "includes": [
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "requests",
        "dotenv",
    ],
    "excludes": [
        "tkinter",
        "matplotlib",
        "scipy",
        "numpy.testing",
        "test",
        "unittest",
    ],
    "iconfile": "assets/AppIcon.icns",
}

setup(
    name=APP_NAME,
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
