#!/usr/bin/env python3
"""Compatibility entry point for the integrated demo commands."""

from __future__ import annotations

import sys

from nova_cli.cli import main


raise SystemExit(main(["demo", *sys.argv[1:]]))
