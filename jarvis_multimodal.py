#!/usr/bin/env python3
"""
Backward-compatibility shim — redirects to ep_agent.py.

The old LaunchAgent (com.escipion.jarvis) points here.
This just imports and runs the new entry point.
"""

from ep_agent import main

if __name__ == "__main__":
    main()
