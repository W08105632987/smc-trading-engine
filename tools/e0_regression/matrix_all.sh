#!/bin/bash
H=/home/claude/e0/harness; ORIG=/home/claude/e0_ORIGINAL_ref/smc-trading-engine; E0=/home/claude/e0/smc-trading-engine-PHASE-A-v2/smc-trading-engine
cd $H; P=$H/matrix_all.progress; touch $P
cmp() { # name engine csv preset
  n=$1; eng=$2; csv=$3; pre=$4
  if grep -q "^$n " $P; then return; fi
  python3 run_scenario.py $ORIG $csv $eng $pre off $H/o_$n > $H/o_$n.log 2>&1
  python3 run_scenario.py $E0 $csv $eng $pre on  $H/e_$n > $H/e_$n.log 2>&1
  python3 run_scenario.py $E0 $csv $eng $pre off $H/f_$n > $H/f_$n.log 2>&1
  r1=$(python3 compare_dirs.py $H/o_$n $H/e_$n | tail -1); r2=$(python3 compare_dirs.py $H/o_$n $H/f_$n | tail -1)
  echo "$n $eng $pre | telemetry ON: $r1 | telemetry OFF: $r2 | $(tail -1 $H/o_$n.log | cut -c1-40)" >> $P
}
for sd in 11 12 13; do for pre in default relaxed relaxed_costs; do cmp fast_s${sd}_$pre FAST $H/t3d_s$sd.csv $pre; done; done
cmp ref_s11_relaxed REFERENCE $H/t2d_ref.csv relaxed
grep -q ALLDONE $P || echo ALLDONE >> $P
