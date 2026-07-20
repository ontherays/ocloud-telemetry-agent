#!/bin/bash
# Auto-detecting capture for the O-DU energy study.
#
#   sudo ./run_campaign.sh            detect current state, capture it, once
#   sudo ./run_campaign.sh --watch    detect + capture continuously (re-detect
#                                      each cycle; labels follow reality)
#
# The framework OBSERVES and RECORDS. It never generates traffic. iperf is
# driven entirely by the r-App; this script only detects that traffic exists
# and labels the run C accordingly.
#
# Detected states:
#   A       gNB down                        -> platform floor
#   B       gNB up, no UE, no iperf         -> DU static cost
#   B-ue    gNB up, UE attached, no iperf   -> attached, no traffic
#   C       gNB up, iperf running           -> load
#   (stop)  pod up but gNB process absent   -> restart loop; report, don't label
#
# Env overrides:
#   NS           k8s namespace           (default ravi-ns)
#   GNB_LABEL    pod label selector      (default app.kubernetes.io/name=ocudu-gnb)
#   GNB_COMM     process name for pgrep  (default gnb)
#   WINDOW       capture seconds         (default 30)
#   METRICS_LOG  host path to gNB metrics log, if one exists (else kubectl)

set -u
NS="${NS:-ravi-ns}"
GNB_LABEL="${GNB_LABEL:-app.kubernetes.io/name=ocudu-gnb}"
GNB_COMM="${GNB_COMM:-gnb}"
WINDOW="${WINDOW:-30}"
METRICS_LOG="${METRICS_LOG:-}"
AGENT="python3 -m agent.once"

log(){ echo "[campaign] $*"; }

# --- detection helpers -----------------------------------------------------

pod_name(){
  command -v kubectl >/dev/null 2>&1 || { echo ""; return; }
  kubectl -n "$NS" get pod -l "$GNB_LABEL" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null
}

pod_running(){
  local p="$1"; [ -z "$p" ] && return 1
  local phase
  phase=$(kubectl -n "$NS" get pod "$p" -o jsonpath='{.status.phase}' 2>/dev/null)
  [ "$phase" = "Running" ]
}

gnb_process_up(){
  # pgrep sees the process only with hostPID or on the host running the gNB.
  pgrep -x "$GNB_COMM" >/dev/null 2>&1 && return 0
  pgrep -f "/$GNB_COMM" >/dev/null 2>&1 && return 0
  return 1
}

# nof_ues: host metrics log if present, else the latest kubectl log line.
read_nof_ues(){
  local line=""
  if [ -n "$METRICS_LOG" ] && [ -f "$METRICS_LOG" ]; then
    line=$(grep -o 'nof_ues=[0-9]*' "$METRICS_LOG" 2>/dev/null | tail -1)
  fi
  if [ -z "$line" ] && command -v kubectl >/dev/null 2>&1; then
    local p; p=$(pod_name)
    if [ -n "$p" ]; then
      line=$(kubectl -n "$NS" logs "$p" -c ocudu-gnb --tail=20 2>/dev/null \
             | grep -o 'nof_ues=[0-9]*' | tail -1)
    fi
  fi
  echo "${line#nof_ues=}"      # empty if unknown
}

iperf_running(){
  pgrep -x iperf3 >/dev/null 2>&1 || pgrep -f 'iperf3 ' >/dev/null 2>&1
}

# --- classifier ------------------------------------------------------------
# echoes: "<label>|<note>"  or  "STOP|<reason>"
classify(){
  local pod pods_run proc ues
  pod=$(pod_name)
  pods_run=1; pod_running "$pod" || pods_run=0
  proc=1;     gnb_process_up   || proc=0

  # gNB DOWN: neither pod nor process
  if [ "$pods_run" -eq 0 ] && [ "$proc" -eq 0 ]; then
    echo "A-idle-no-gnb|no pod, no process"; return
  fi

  # DISAGREEMENT: pod Running but no gnb process (or vice versa) = restart loop
  if [ "$pods_run" -ne "$proc" ]; then
    echo "STOP|pod_running=$pods_run but gnb_process=$proc (restart loop / O1 timeout?) - not labelling"
    return
  fi

  # gNB UP. Traffic?
  if iperf_running; then
    echo "C-iperf|iperf3 running (rApp-driven); nof_ues=$(read_nof_ues)"; return
  fi

  # UP, no traffic. UE attached?
  ues=$(read_nof_ues)
  if [ -z "$ues" ]; then
    echo "B-gnb-idle|gNB up, no iperf, nof_ues=UNKNOWN (metrics source unreachable)"; return
  fi
  if [ "$ues" -gt 0 ] 2>/dev/null; then
    echo "B-ue-gnb-attached|gNB up, UE attached (nof_ues=$ues), no traffic"; return
  fi
  echo "B-gnb-idle|gNB up, no UE (nof_ues=0), no traffic"
}

# --- capture ---------------------------------------------------------------
capture(){
  local label="$1" note="$2"
  log "state: $label"
  log "why:   $note"
  log "capturing ${WINDOW}s ..."
  ENABLE_UNCORE=true ENABLE_PERF=true \
    sudo -E $AGENT --label "$label" --window "$WINDOW"
}

do_one(){
  local res label note
  res=$(classify)
  label="${res%%|*}"; note="${res#*|}"
  if [ "$label" = "STOP" ]; then
    log "STOP: $note"
    log "Refusing to capture an ambiguous state. Fix the gNB, then re-run."
    return 2
  fi
  capture "$label" "$note"
}

# --- main ------------------------------------------------------------------
case "${1:-}" in
  --watch)
    log "watch mode: re-detect + capture each cycle. Ctrl-C to stop."
    while true; do do_one || true; log "--- cycle done ---"; done
    ;;
  ""|--once)
    do_one
    ;;
  *)
    echo "usage: sudo $0 [--once|--watch]"; exit 1
    ;;
esac
