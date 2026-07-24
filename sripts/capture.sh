#!/bin/bash
# Energy-ladder capture with state verification.
#
#   sudo ./capture.sh <stack> <condition> [rate]
#
#     stack      ocudu | oai
#     condition  A  no gNB
#                B  gNB up, no UE
#                C  gNB up, UE attached, no traffic
#                D  gNB up, UE + iperf   (rate required, e.g. 100M)
#
#   examples:
#     sudo ./capture.sh ocudu A
#     sudo ./capture.sh ocudu B
#     sudo ./capture.sh oai   C
#     sudo ./capture.sh ocudu D 200M
#
# Runs 3 captures, verifies the system state matches the declared condition
# BEFORE capturing, and prints per-run + mean package-1 watts at the end.
#
# Env overrides: RUNS=3  WINDOW=30  AGENT_DIR=~/ocloud-telemetry-agent

set -u
RUNS="${RUNS:-3}"
WINDOW="${WINDOW:-30}"
# Resolve the agent dir from THIS script's location, not $HOME.
# ($HOME becomes /root under sudo, which broke the old default.)
_SD="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if   [ -d "$_SD/agent" ];    then _DEFAULT_AGENT="$_SD"
elif [ -d "$_SD/../agent" ]; then _DEFAULT_AGENT="$(cd "$_SD/.." && pwd)"
else _DEFAULT_AGENT="$_SD"; fi
AGENT_DIR="${AGENT_DIR:-$_DEFAULT_AGENT}"
RUNS_DIR="${RUNS_DIR:-/mnt/debugging-logs/runs}"

die(){ echo "ERROR: $*" >&2; exit 1; }
say(){ echo "[capture] $*"; }

# ---- args ------------------------------------------------------------------
STACK="${1:-}"; COND="${2:-}"; RATE="${3:-}"
case "$STACK" in ocudu|oai) ;; *) die "stack must be 'ocudu' or 'oai' (usage: $0 <stack> <A|B|C|D> [rate])";; esac
case "$COND"  in A|B|C|D) ;;  *) die "condition must be A, B, C or D";; esac
[ "$COND" = "D" ] && [ -z "$RATE" ] && die "condition D needs a rate, e.g. '$0 $STACK D 100M'"

# ---- per-stack settings ----------------------------------------------------
if [ "$STACK" = "ocudu" ]; then
    PROC="gnb"
    OTHER_PROC="nr-softmodem"
    ENV_EXTRA=(GNB_COMM=gnb ENABLE_PERF=true ENABLE_UNCORE=true
               GNB_CONFIG_PATH=/mnt/gnb-runtime/gnb-config.yml)
else
    PROC="nr-softmodem"
    OTHER_PROC="gnb"
    # OAI monopolises the PMU with its own perf stat -> disable perf/uncore
    ENV_EXTRA=(GNB_COMM=nr-softmodem ENABLE_PERF=false ENABLE_UNCORE=false)
fi

# ---- label -----------------------------------------------------------------
case "$COND" in
  A) LABEL="A-${STACK}-no-gnb" ;;
  B) LABEL="B-${STACK}-noUE" ;;
  C) LABEL="C-${STACK}-ue-noTraffic" ;;
  D) LABEL="D-${STACK}-iperf-${RATE}" ;;
esac

# ---- state verification ----------------------------------------------------
say "stack=$STACK condition=$COND label=$LABEL runs=$RUNS window=${WINDOW}s"
say "verifying system state ..."

gnb_pid=$(pgrep -x "$PROC" | head -1)
other_pid=$(pgrep -x "$OTHER_PROC" | head -1)

if [ -n "$other_pid" ]; then
    die "the OTHER stack is running ($OTHER_PROC, pid $other_pid). Stop it first - mixing stacks invalidates the ladder."
fi

if [ "$COND" = "A" ]; then
    [ -n "$gnb_pid" ] && die "condition A requires NO gNB, but $PROC is running (pid $gnb_pid)."
    say "  OK: no $PROC and no $OTHER_PROC running"
else
    [ -z "$gnb_pid" ] && die "condition $COND requires the gNB running, but no $PROC process found."
    say "  OK: $PROC running (pid $gnb_pid)"
fi

# iperf check (best-effort: iperf may run on another host)
if pgrep -x iperf3 >/dev/null 2>&1; then
    IPERF_LOCAL="yes"
else
    IPERF_LOCAL="no"
fi
if [ "$COND" = "D" ]; then
    say "  iperf3 on this host: $IPERF_LOCAL  (traffic is usually generated remotely - confirm it is FLOWING now)"
    read -r -p "  Confirm iperf is actively running at ${RATE} and will cover the next $((RUNS*WINDOW))s [y/N]: " ok
    [ "$ok" = "y" ] || [ "$ok" = "Y" ] || die "aborted - start iperf first"
elif [ "$COND" = "C" ]; then
    [ "$IPERF_LOCAL" = "yes" ] && die "condition C requires NO traffic, but iperf3 is running on this host."
    read -r -p "  Confirm a UE IS attached and NO iperf is running anywhere [y/N]: " ok
    [ "$ok" = "y" ] || [ "$ok" = "Y" ] || die "aborted - verify UE attachment first"
elif [ "$COND" = "B" ]; then
    [ "$IPERF_LOCAL" = "yes" ] && die "condition B requires no traffic, but iperf3 is running on this host."
    read -r -p "  Confirm NO UE is attached [y/N]: " ok
    [ "$ok" = "y" ] || [ "$ok" = "Y" ] || die "aborted - detach the UE first"
fi

# ---- capture ---------------------------------------------------------------
cd "$AGENT_DIR" || die "agent dir not found: $AGENT_DIR"
say "capturing $RUNS x ${WINDOW}s ..."
declare -a MADE=()
for i in $(seq 1 "$RUNS"); do
    say "  run $i/$RUNS"
    before=$(ls -1 "$RUNS_DIR" 2>/dev/null | wc -l)
    env "${ENV_EXTRA[@]}" python3 -m agent.once --label "$LABEL" --window "$WINDOW" >/dev/null 2>&1 \
        || die "capture failed on run $i (try running the agent manually to see the error)"
    newest=$(ls -dt "$RUNS_DIR"/*"$LABEL"* 2>/dev/null | head -1)
    MADE+=("$newest")
done

# ---- report ----------------------------------------------------------------
echo
say "results for $LABEL"
printf "%-52s %10s %10s\n" "run" "pkg1(W)" "pkg0(W)"
tot1=0; tot0=0; n=0
for D in "${MADE[@]}"; do
    p1=$(awk -F, '$5=="package-1"{s+=$8;c++} END{if(c)printf "%.2f",s/c}' "$D/power.csv" 2>/dev/null)
    p0=$(awk -F, '$5=="package-0"{s+=$8;c++} END{if(c)printf "%.2f",s/c}' "$D/power.csv" 2>/dev/null)
    printf "%-52s %10s %10s\n" "$(basename "$D")" "${p1:-n/a}" "${p0:-n/a}"
    if [ -n "${p1:-}" ]; then
        tot1=$(awk -v a="$tot1" -v b="$p1" 'BEGIN{printf "%.4f",a+b}')
        tot0=$(awk -v a="$tot0" -v b="${p0:-0}" 'BEGIN{printf "%.4f",a+b}')
        n=$((n+1))
    fi
done
if [ "$n" -gt 0 ]; then
    echo "$(printf '%.0s-' {1..74})"
    awk -v t1="$tot1" -v t0="$tot0" -v n="$n" \
        'BEGIN{printf "%-52s %10.2f %10.2f\n","MEAN of "n" runs",t1/n,t0/n}'
fi
echo
say "done. pkg0 (control) should be ~64 W in every condition."