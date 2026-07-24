#!/bin/bash
# Summarise captured energy-ladder runs.
#
#   ./analyze.sh              all A-/B-/C-/D- runs, grouped by label
#   ./analyze.sh ocudu        only ocudu runs
#   ./analyze.sh oai          only oai runs
#   ./analyze.sh 20260723     only runs from that date prefix
#
# For each label it prints every run's mean, then the group mean, spread and
# sample count. Each run's value is the mean of ALL samples in its power.csv.

set -u
RUNS_DIR="${RUNS_DIR:-/mnt/debugging-logs/runs}"
FILTER="${1:-}"

cd "$RUNS_DIR" 2>/dev/null || { echo "no runs dir: $RUNS_DIR"; exit 1; }

# collect: label<TAB>rundir<TAB>pkg1<TAB>pkg0
tmp=$(mktemp)
for D in */; do
    D="${D%/}"
    [ -f "$D/power.csv" ] || continue
    case "$D" in *A-*|*B-*|*C-*|*D-*) ;; *) continue ;; esac
    [ -n "$FILTER" ] && case "$D" in *"$FILTER"*) ;; *) continue ;; esac
    p1=$(awk -F, '$5=="package-1"{s+=$8;c++} END{if(c)printf "%.2f",s/c}' "$D/power.csv")
    p0=$(awk -F, '$5=="package-0"{s+=$8;c++} END{if(c)printf "%.2f",s/c}' "$D/power.csv")
    [ -z "$p1" ] && continue
    # label = run dir minus leading timestamp and trailing hex id
    lbl=$(echo "$D" | sed -E 's/^[0-9]{8}T[0-9]{6}Z-//; s/-[0-9a-f]{6}$//')
    printf "%s\t%s\t%s\t%s\n" "$lbl" "$D" "$p1" "${p0:-0}" >> "$tmp"
done

[ -s "$tmp" ] || { echo "no matching runs"; rm -f "$tmp"; exit 0; }

echo "=============================================================================="
printf "%-30s %10s %10s %8s\n" "CONDITION / run" "pkg1(W)" "pkg0(W)" ""
echo "=============================================================================="

# per-label detail + summary
cut -f1 "$tmp" | sort -u | while read -r lbl; do
    echo
    echo "--- $lbl ---"
    awk -F'\t' -v L="$lbl" '$1==L' "$tmp" |
    while IFS=$'\t' read -r l d p1 p0; do
        printf "  %-40s %10s %10s\n" "$d" "$p1" "$p0"
    done
    awk -F'\t' -v L="$lbl" '$1==L {s1+=$3; s0+=$4; n++;
        if(mn==""||$3<mn)mn=$3; if(mx==""||$3>mx)mx=$3}
        END{ if(n) printf "  %-40s %10.2f %10.2f   (n=%d, spread %.2f-%.2f)\n",
             "MEAN", s1/n, s0/n, n, mn, mx }' "$tmp"
done

echo
echo "=============================================================================="
echo "LADDER SUMMARY (group means, sorted by energy)"
echo "=============================================================================="
awk -F'\t' '{s1[$1]+=$3; s0[$1]+=$4; n[$1]++}
     END{for(l in n) printf "%.2f\t%.2f\t%d\t%s\n", s1[l]/n[l], s0[l]/n[l], n[l], l}' "$tmp" \
  | sort -n \
  | awk -F'\t' 'BEGIN{printf "%-32s %10s %10s %6s\n","condition","pkg1(W)","pkg0(W)","runs"}
       {printf "%-32s %10s %10s %6s\n",$4,$1,$2,$3}'

echo
echo "checks:  ladder should INCREASE A < B < C <= D"
echo "         pkg0 (control) should stay ~64 W in every condition"
rm -f "$tmp"