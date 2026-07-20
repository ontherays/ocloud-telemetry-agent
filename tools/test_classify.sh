#!/bin/bash
set -u
# stub the agent + sudo so nothing real runs
AGENT="true"; WINDOW=1

# import just the classifier + helpers from the script (skip the main case block)
source <(sed '/^# --- main/,$d' "$(dirname "$0")/run_campaign.sh")

pass=0; fail=0
expect(){ # $1=expected label  $2=actual "label|note"
  local got="${2%%|*}"
  if [ "$got" = "$1" ]; then echo "  PASS  $1  ($2)"; pass=$((pass+1))
  else echo "  FAIL  expected $1 got $got  ($2)"; fail=$((fail+1)); fi
}

echo "State A: gNB down"
pod_name(){ echo ""; }; pod_running(){ return 1; }; gnb_process_up(){ return 1; }
iperf_running(){ return 1; }; read_nof_ues(){ echo ""; }
expect "A-idle-no-gnb" "$(classify)"

echo "STOP: pod running, process absent (restart loop)"
pod_name(){ echo "pod-x"; }; pod_running(){ return 0; }; gnb_process_up(){ return 1; }
expect "STOP" "$(classify)"

echo "State C: gNB up + iperf running"
pod_name(){ echo "pod-x"; }; pod_running(){ return 0; }; gnb_process_up(){ return 0; }
iperf_running(){ return 0; }; read_nof_ues(){ echo "1"; }
expect "C-iperf" "$(classify)"

echo "State B: gNB up, no iperf, nof_ues=0"
iperf_running(){ return 1; }; read_nof_ues(){ echo "0"; }
expect "B-gnb-idle" "$(classify)"

echo "State B-ue: gNB up, no iperf, nof_ues=3"
read_nof_ues(){ echo "3"; }
expect "B-ue-gnb-attached" "$(classify)"

echo "State B (ues unknown): metrics source unreachable"
read_nof_ues(){ echo ""; }
expect "B-gnb-idle" "$(classify)"

echo
echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
