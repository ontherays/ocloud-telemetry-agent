#!/bin/bash
# One-command launcher for the InfluxDB shipper on Galileo.
# Loads config from /etc/ocloud-shipper.env so you never hand-set env vars.
#
#   ./ship.sh once     ship any new runs, then exit
#   ./ship.sh watch     ship continuously every SHIP_INTERVAL (default 60s)
#   ./ship.sh reship    force re-ship everything (idempotent)
#
# The systemd service (ocloud-shipper) already runs 'watch' persistently and
# survives reboots. Use this script for manual/one-off ships, or if you prefer
# to run the shipper by hand instead of via systemd.

set -u
ENV_FILE="${ENV_FILE:-/etc/ocloud-shipper.env}"
SHIPPER="${SHIPPER:-/root/ocloud-influx-shipper/influx-ship.py}"
MODE="${1:-once}"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: env file not found: $ENV_FILE"
    echo "Create it (see GUIDE.md) with INFLUX_URL/ORG/BUCKET/TOKEN + RSYNC_RSH."
    exit 1
fi

# load env (strip comments/blank lines; export each KEY=VALUE)
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

# sanity
for v in INFLUX_URL INFLUX_ORG INFLUX_BUCKET INFLUX_TOKEN; do
    if [ -z "${!v:-}" ]; then
        echo "ERROR: $v not set in $ENV_FILE"; exit 1
    fi
done

echo "[ship.sh] URL=$INFLUX_URL org=$INFLUX_ORG bucket=$INFLUX_BUCKET mode=$MODE"
exec python3 "$SHIPPER" "$MODE"