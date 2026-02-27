#!/usr/bin/env python3
"""DSL cron entry point. Single-position mode: runs dsl-v4 (DSL_STATE_FILE).
Strategy/multi mode will be added in P1."""
import os
import sys
script_dir = os.path.dirname(os.path.abspath(__file__))
dsl_v4_path = os.path.join(script_dir, "dsl-v4.py")
os.execv(sys.executable, [sys.executable, dsl_v4_path] + sys.argv[1:])
