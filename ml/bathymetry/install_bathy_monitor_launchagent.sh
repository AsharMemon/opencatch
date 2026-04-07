#!/usr/bin/env bash
set -euo pipefail

LABEL="com.openclaw.bathy-monitor"
SRC="/Users/Ashar/Documents/fish/ml/bathymetry/${LABEL}.plist"
DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
USER_UID="$(id -u)"

mkdir -p "${HOME}/Library/LaunchAgents" "/Users/Ashar/Documents/fish/.claude/logs"
cp "${SRC}" "${DST}"

launchctl bootout "gui/${USER_UID}" "${DST}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${USER_UID}" "${DST}"
launchctl enable "gui/${USER_UID}/${LABEL}" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/${USER_UID}/${LABEL}"

echo "Installed ${LABEL} -> ${DST}"
echo "StartInterval: 1200 seconds"
