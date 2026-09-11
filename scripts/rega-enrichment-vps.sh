#!/usr/bin/env bash
set -euo pipefail
rega_repo="$(rtk git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
rega_state="/home/hameedo/projects/ai-job-search/runtime/acceptance/rega-api-enrichment-20260911"
rega_unit="career-rega-api-enrichment"
case "${1:-status}" in
  start|pilot)
    rega_mode="$1"
    shift
    rega_args=(--limit 0 --search-limit 4200 --hunter-cap 60 --prospeo-cap 60 --snov-cap 500)
    if [[ "$rega_mode" == "pilot" ]]; then
      rega_args=(--pilot --limit 12 --hunter-cap 15 --prospeo-cap 20 --snov-cap 50)
    fi
    rtk python3 "$rega_repo/career-engine" doctor >/dev/null
    rtk python3 "$rega_repo/career-engine" bundle status >/dev/null
    rtk mkdir -p "$rega_state"
    rtk systemd-run --user --unit="$rega_unit" --collect \
      --working-directory="$rega_repo" \
      --setenv="PATH=$PATH" \
      --property=RuntimeMaxSec=6h --property=TimeoutStopSec=20 \
      --property=KillMode=control-group --property=UMask=0077 \
      --property="StandardOutput=append:$rega_state/run.log" \
      --property="StandardError=append:$rega_state/run.log" \
      /home/hameedo/.local/bin/rtk proxy python3 \
      /home/hameedo/vps-infra-dev/scripts/infisical-vps/runtime_exec.py \
      --manifest "$rega_repo/projects/job-automation/config/rega-contact-runtime-manifest.json" \
      -- python3 "$rega_repo/career-engine" rega-enrich --root "$rega_state" \
      --apply "${rega_args[@]}" "$@"
    ;;
  status)
    rtk systemctl --user show "$rega_unit.service" -p ActiveState -p SubState -p Result -p ExecMainStatus
    rtk python3 "$rega_repo/career-engine" rega-enrich --root "$rega_state" --status
    ;;
  stop)
    rtk systemctl --user stop "$rega_unit.service"
    ;;
  *)
    echo "Usage: scripts/rega-enrichment-vps.sh {pilot|start|status|stop}"
    exit 2
    ;;
esac
