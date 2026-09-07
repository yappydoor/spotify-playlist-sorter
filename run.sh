#!/bin/bash
cd "$(dirname "$0")"
unset VIRTUAL_ENV
exec ./.venv/bin/python sort_playlist.py "$@"
