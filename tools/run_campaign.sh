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

pod_phase(){
  # Echoes the pod phase, or "" if kubectl can't reach the cluster / no pod.
  # On joule, kubectl targets a different context than the KVM that manages the
  # pod, so this is often "" even when the gNB runs here. That is NOT a fault --
  # pgrep is the ground truth for liveness on the workload node.
  command -v kubectl >/dev/null 2>&1 || { echo ""; return; }
  local p; p=$(pod_name)
  [ -z "$p" ] && { echo ""; return; }
  kubectl -n "$NS" get pod "$p" -o jsonpath='{.status.phase}' 2>/dev/null
}

gnb_process_up(){
  # Ground truth on the node running the workload. The DU binary is `gnb`
  # (srsRAN), invoked as `gnb -c /tmp/gnb-config.yml` on joule.
  pgrep -x "$GNB_COMM" >/dev/null 2>&1 && return 0
  pgrep -f "^$GNB_COMM -c" >/dev/null 2>&1 && return 0
  pgrep -f "/$GNB_COMM " >/dev/null 2>&1 && return 0
  return 1
}

# nof_ues: host metrics log if present, else kubectl. The gNB tees stdout to a
# host file under a per-RESTART timestamped dir, e.g.
#   /var/rootdirs/mnt/debugging-logs/<YYYYMMDD-HHMMSS>/gnb.stdout
# (ostree prefixes /var/rootdirs). The dir name changes each restart, so we must
# pick the NEWEST gnb.stdout, not a hardcoded path. METRICS_LOG overrides.
_discover_metrics_log(){
  if [ -n "$METRICS_LOG" ]; then
    [ -f "$METRICS_LOG" ] && echo "$METRICS_LOG"
    return
  fi
  # newest gnb.stdout across the known roots (ostree /var/rootdirs first)
  ls -t \
    /var/rootdirs/mnt/debugging-logs/*/gnb.stdout \
    /mnt/debugging-logs/*/gnb.stdout \
    2>/dev/null | head -1
}

read_nof_ues(){
  local line="" f
  f=$(_discover_metrics_log)
  if [ -n "$f" ] && [ -f "$f" ]; then
    # only trust a log that is actually current: modified in the last 120s.
    # a stale file from an old restart must NOT drive classification.
    if [ -n "$(find "$f" -mmin -2 2>/dev/null)" ]; then
      line=$(grep -o 'nof_ues=[0-9]*' "$f" 2>/dev/null | tail -1)
    fi
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
#
# Liveness = the gNB PROCESS (pgrep). This is ground truth on the node running
# the workload, and is RAN-agnostic (OAI bare-metal has no pod either). The pod
# phase only ENRICHES: it can flag an explicit fault, but its absence (kubectl
# unreachable from this host) never overrides pgrep.
classify(){
  local proc phase ues
  proc=1; gnb_process_up || proc=0
  phase=$(pod_phase)      # "" if kubectl unreachable / no pod (normal on joule)

  # gNB DOWN: no process.
  if [ "$proc" -eq 0 ]; then
    # If kubectl explicitly reports the pod Running while no process exists,
    # THAT is the real restart-loop / wrong-node signal worth stopping on.
    if [ "$phase" = "Running" ]; then
      echo "STOP|pod phase=Running but no gnb process here (wrong node, or O1-timeout restart loop)"
      return
    fi
    echo "A-idle-no-gnb|no gnb process (pod phase='${phase:-unknown}')"; return
  fi

  # gNB process IS up. A non-Running explicit phase is a genuine anomaly.
  if [ -n "$phase" ] && [ "$phase" != "Running" ]; then
    echo "STOP|gnb process up but pod phase=$phase (anomalous) - not labelling"
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
