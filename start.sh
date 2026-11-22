#!/bin/sh
cd "$(dirname "$0")" && exec .venv/bin/python -m verse_screen "$@"
