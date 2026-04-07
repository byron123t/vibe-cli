#!/usr/bin/env python3
"""
VibeSwipe — keyboard-first multi-project Claude Code TUI.

Usage:
    python main.py [--config PATH] [--add PATH ...]

Keys:
    Alt+] / Alt+[              — next / prev project
    Alt+1 … Alt+9             — jump to project by number
    Ctrl+N                     — new Claude agent (opens prompt bar)
    Ctrl+W                     — close last agent
    Ctrl+E                     — focus editor
    Ctrl+B                     — focus sidebar file tree
    Ctrl+G                     — toggle knowledge graph view
    Ctrl+S                     — save current file
    Ctrl+P                     — focus prompt bar
    1-4                        — fill suggestion into prompt
    Enter                      — execute prompt / submit
    Ctrl+Q                     — quit
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def load_config(path: str) -> dict:
    if os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="VibeSwipe — multi-project Claude TUI")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--add", nargs="*", metavar="PATH",
                        help="Project directories to open on startup")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = args.config if os.path.isabs(args.config) else \
                  os.path.join(script_dir, args.config)

    config = load_config(config_path)
    os.chdir(script_dir)

    from ui.app import VibeSwipeApp
    from core.project_manager import ProjectManager

    # Pre-add any projects from --add flag
    if args.add:
        pm = ProjectManager()
        for p in args.add:
            path = os.path.abspath(p)
            if os.path.isdir(path):
                pm.add_project(path)
                print(f"  Added project: {path}")
            else:
                print(f"  [!] Not a directory, skipping: {path}")

    app = VibeSwipeApp(config)
    app.run()


if __name__ == "__main__":
    main()
