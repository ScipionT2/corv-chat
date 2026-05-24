#!/bin/bash
echo "Fixing Nova AI for macOS..."
xattr -cr "/Applications/Nova AI.app" 2>/dev/null || xattr -cr ~/Downloads/Nova*.app 2>/dev/null
echo "Done! You can now open Nova AI."
