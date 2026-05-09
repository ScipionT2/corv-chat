#!/usr/bin/env python3
"""
Generate the EP Agent app icon (.icns) from a rendered ⚡ emoji.

Creates a simple dark circle with a lightning bolt — suitable for
macOS Dock and menu bar display.

Output: assets/AppIcon.icns
"""

import os
import subprocess
import sys
import tempfile

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("❌ Pillow required: pip install Pillow")
    sys.exit(1)


def generate_icon():
    """Generate the app icon as .icns."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets_dir = os.path.join(project_root, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    # Create a 1024x1024 icon (macOS standard)
    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Dark circular background
    margin = 40
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(30, 30, 46, 255),  # Dark background (#1e1e2e)
        outline=(69, 71, 90, 255),  # Subtle border
        width=8,
    )

    # Draw ⚡ lightning bolt as polygon (since emoji fonts are unreliable)
    # Lightning bolt shape centered in the circle
    cx, cy = size // 2, size // 2
    scale = 2.8
    bolt_points = [
        (cx - 60 * scale, cy - 10 * scale),   # top-left
        (cx + 20 * scale, cy - 10 * scale),   # top connector
        (cx - 10 * scale, cy + 40 * scale),   # middle-left
        (cx + 80 * scale, cy - 100 * scale),  # top-right peak
        (cx - 20 * scale, cy + 10 * scale),   # middle connector
        (cx + 10 * scale, cy - 40 * scale),   # upper middle
        (cx - 80 * scale, cy + 100 * scale),  # bottom-left peak
    ]
    # Simplified lightning bolt
    bolt = [
        (cx - 40, cy - 200),
        (cx + 60, cy - 200),
        (cx + 10, cy - 20),
        (cx + 120, cy - 20),
        (cx - 60, cy + 240),
        (cx - 10, cy + 40),
        (cx - 80, cy + 40),
    ]
    draw.polygon(bolt, fill=(0, 188, 212, 255))  # Cyan (#00BCD4)

    # Save as PNG first
    png_path = os.path.join(assets_dir, "AppIcon.png")
    img.save(png_path, "PNG")

    # Convert to .icns using macOS iconutil
    icns_path = os.path.join(assets_dir, "AppIcon.icns")

    # Create iconset directory with required sizes
    with tempfile.TemporaryDirectory() as tmpdir:
        iconset_dir = os.path.join(tmpdir, "AppIcon.iconset")
        os.makedirs(iconset_dir)

        sizes = [16, 32, 64, 128, 256, 512, 1024]
        for s in sizes:
            resized = img.resize((s, s), Image.Resampling.LANCZOS)
            resized.save(os.path.join(iconset_dir, f"icon_{s}x{s}.png"))
            # @2x versions
            if s <= 512:
                resized2x = img.resize((s * 2, s * 2), Image.Resampling.LANCZOS)
                resized2x.save(os.path.join(iconset_dir, f"icon_{s}x{s}@2x.png"))

        # Use iconutil to convert
        try:
            subprocess.run(
                ["iconutil", "-c", "icns", iconset_dir, "-o", icns_path],
                check=True,
                capture_output=True,
            )
            print(f"✅ Generated: {icns_path}")
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback: just keep the PNG (py2app can use it)
            print(f"⚠️  iconutil failed — using PNG: {png_path}")
            # Copy PNG as fallback
            import shutil
            shutil.copy2(png_path, icns_path.replace(".icns", ".png"))

    return icns_path


if __name__ == "__main__":
    generate_icon()
