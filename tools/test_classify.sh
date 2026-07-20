#!/bin/bash
set -u
AGENT="true"; WINDOW=1
source <(sed '/^# --- main/,$d' "$(dirname "$0")/run_campaign.sh")

pass=0; fail=0
expect(){ local got="${2%%|*}"
  if [ "$got" = "$1" ]; then echo "  PASS  $1"; pass=$((pass+1))
  else echo "  FAIL  expected $1 got $got  ($2)"; fail=$((fail+1)); fi; }

# Liveness is pgrep (gnb_process_up); pod_phase only enriches.

echo "A: no process, kubectl unreachable (normal on joule)"
gnb_process_up(){ return 1; }; pod_phase(){ echo ""; }
iperf_running(){ return 1; }; read_nof_ues(){ echo "0"; }
expect "A-idle-no-gnb" "$(classify)"

echo "STOP: no process but pod explicitly Running (wrong node / restart loop)"
gnb_process_up(){ return 1; }; pod_phase(){ echo "Running"; }
expect "STOP" "$(classify)"

echo "C: process up + iperf running (kubectl unreachable)"
gnb_process_up(){ return 0; }; pod_phase(){ echo ""; }
iperf_running(){ return 0; }; read_nof_ues(){ echo "1"; }
expect "C-iperf" "$(classify)"

echo "B: process up, no iperf, nof_ues=0"
iperf_running(){ return 1; }; read_nof_ues(){ echo "0"; }
expect "B-gnb-idle" "$(classify)"

echo "B-ue: process up, no iperf, nof_ues=3"
read_nof_ues(){ echo "3"; }
expect "B-ue-gnb-attached" "$(classify)"

echo "STOP: process up but pod phase=Failed (anomaly)"
read_nof_ues(){ echo "0"; }; pod_phase(){ echo "Failed"; }
expect "STOP" "$(classify)"

echo "B: process up, kubectl unreachable, ues unknown"
pod_phase(){ echo ""; }; read_nof_ues(){ echo ""; }
expect "B-gnb-idle" "$(classify)"

echo; echo "pass=$pass fail=$fail"; [ "$fail" -eq 0 ]
