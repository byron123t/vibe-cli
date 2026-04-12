#!/usr/bin/env python3
"""
vibe-cli — keyboard-first multi-project AI coding terminal.

Usage:
    vibe [--config PATH] [--add PATH ...]
    python main.py [--config PATH] [--add PATH ...]

Keys (command mode):
    ] / [       next / prev project
    1–9         jump to project by number
    n / Enter   new agent prompt
    x           cancel last agent
    d           dismiss last agent
    j / k       scroll agents down / up
    f           toggle file browser
    e           toggle editor
    i           enter edit mode
    m           toggle memory graph
    t           toggle terminal
    r           run last detected shell command
    s           save file
    A           cycle agent type (Claude / Codex / Cursor)
    P           cycle permission mode (Safe / Accept Edits / Bypass)
    o           open project (directory picker)
    q           quit
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
    parser = argparse.ArgumentParser(description="vibe-cli — multi-project AI coding terminal")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--add", nargs="*", metavar="PATH",
                        help="Project directories to open on startup")
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = args.config if os.path.isabs(args.config) else \
                  os.path.join(script_dir, args.config)

    config = load_config(config_path)
    os.chdir(script_dir)

    from ui.app import VibeCLIApp
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

    app = VibeCLIApp(config, config_path=config_path)
    app.run()


if __name__ == "__main__":
    main()
