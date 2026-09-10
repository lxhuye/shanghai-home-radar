#!/usr/bin/env bash
set -Eeuo pipefail

backup_directory="${SHR_BACKUP_DIRECTORY:-/backups}"
schedule_time="${SHR_BACKUP_SCHEDULE_TIME:-01:00}"
timezone="${SHR_DAILY_TIMEZONE:-Asia/Shanghai}"
run_on_start="${SHR_BACKUP_RUN_ON_START:-false}"
catch_up_on_start="${SHR_BACKUP_CATCH_UP_ON_START:-true}"
backup_script="${SHR_BACKUP_SCRIPT:-/opt/home-radar/database_backup.sh}"

if [[ ! "${schedule_time}" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  echo "SHR_BACKUP_SCHEDULE_TIME must use HH:MM in 24-hour time" >&2
  exit 2
fi
if [[ "${run_on_start}" != "true" && "${run_on_start}" != "false" ]]; then
  echo "SHR_BACKUP_RUN_ON_START must be true or false" >&2
  exit 2
fi
if [[ "${catch_up_on_start}" != "true" && "${catch_up_on_start}" != "false" ]]; then
  echo "SHR_BACKUP_CATCH_UP_ON_START must be true or false" >&2
  exit 2
fi

export TZ="${timezone}"
mkdir -p "${backup_directory}"
heartbeat_path="${backup_directory}/.scheduler-heartbeat"
success_date_path="${backup_directory}/.last-successful-backup-date"

write_heartbeat() {
  date -u +%Y-%m-%dT%H:%M:%SZ >"${heartbeat_path}"
}

run_backup() {
  echo "backup_scheduler_start local_time=$(date +%Y-%m-%dT%H:%M:%S%z)"
  bash "${backup_script}"
  marker_tmp="${success_date_path}.tmp"
  date +%Y-%m-%d >"${marker_tmp}"
  mv "${marker_tmp}" "${success_date_path}"
  write_heartbeat
  echo "backup_scheduler_success local_date=$(date +%Y-%m-%d)"
}

wait_until() {
  target_epoch="$1"
  while true; do
    now_epoch="$(date +%s)"
    if (( now_epoch >= target_epoch )); then
      return
    fi
    write_heartbeat
    remaining=$((target_epoch - now_epoch))
    sleep_seconds="${remaining}"
    if (( sleep_seconds > 60 )); then
      sleep_seconds=60
    fi
    sleep "${sleep_seconds}"
  done
}

write_heartbeat
today="$(date +%Y-%m-%d)"
last_success_date=""
if [[ -f "${success_date_path}" ]]; then
  last_success_date="$(tr -d '[:space:]' <"${success_date_path}")"
fi

scheduled_epoch_today="$(date -d "${today} ${schedule_time}:00" +%s)"
if [[ "${run_on_start}" == "true" ]]; then
  run_backup
elif [[ "${catch_up_on_start}" == "true" ]] \
  && (( $(date +%s) >= scheduled_epoch_today )) \
  && [[ "${last_success_date}" != "${today}" ]]; then
  run_backup
fi

while true; do
  now_epoch="$(date +%s)"
  today="$(date +%Y-%m-%d)"
  next_epoch="$(date -d "${today} ${schedule_time}:00" +%s)"
  if (( next_epoch <= now_epoch )); then
    next_epoch="$(date -d "tomorrow ${schedule_time}:00" +%s)"
  fi
  echo "backup_scheduler_wait next_run=$(date -d "@${next_epoch}" +%Y-%m-%dT%H:%M:%S%z)"
  wait_until "${next_epoch}"
  run_backup
done
