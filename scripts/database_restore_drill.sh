#!/usr/bin/env bash
set -Eeuo pipefail

: "${PGHOST:?PGHOST is required}"
: "${PGDATABASE:?PGDATABASE is required}"
: "${PGUSER:?PGUSER is required}"

backup_directory="${SHR_BACKUP_DIRECTORY:-/backups}"
requested_backup="${SHR_RESTORE_BACKUP_PATH:-${backup_directory}/latest.dump}"

if [[ -z "${backup_directory}" || "${backup_directory}" == "/" ]]; then
  echo "unsafe backup directory" >&2
  exit 2
fi
if [[ ! -f "${requested_backup}" ]]; then
  echo "backup does not exist: ${requested_backup}" >&2
  exit 2
fi

resolved_backup="$(readlink -f "${requested_backup}")"
case "${resolved_backup}" in
  "${backup_directory%/}/"*) ;;
  *)
    echo "backup must be inside SHR_BACKUP_DIRECTORY" >&2
    exit 2
    ;;
esac

checksum_path="${resolved_backup}.sha256"
if [[ ! -f "${checksum_path}" ]]; then
  echo "checksum file does not exist: ${checksum_path}" >&2
  exit 2
fi

(
  cd "$(dirname "${resolved_backup}")"
  sha256sum --check "$(basename "${checksum_path}")"
)
pg_restore --list "${resolved_backup}" >/dev/null

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
drill_database="homeradar_restore_drill_${timestamp}_$$"
created=false

cleanup_drill_database() {
  if [[ "${created}" == "true" ]]; then
    dropdb --if-exists --force "${drill_database}" >/dev/null 2>&1 || true
  fi
}
trap cleanup_drill_database EXIT

createdb "${drill_database}"
created=true
pg_restore \
  --exit-on-error \
  --no-owner \
  --no-acl \
  --dbname="${drill_database}" \
  "${resolved_backup}"

core_tables_present="$(
  psql --dbname="${drill_database}" --no-align --tuples-only --set=ON_ERROR_STOP=1 \
    --command="SELECT (to_regclass('public.listing') IS NOT NULL
      AND to_regclass('public.crawl_run') IS NOT NULL
      AND to_regclass('public.decision_assessment') IS NOT NULL)::int;"
)"
if [[ "${core_tables_present}" != "1" ]]; then
  echo "restored database is missing one or more core tables" >&2
  exit 1
fi

listing_count="$(
  psql --dbname="${drill_database}" --no-align --tuples-only --set=ON_ERROR_STOP=1 \
    --command="SELECT count(*) FROM listing;"
)"
crawl_run_count="$(
  psql --dbname="${drill_database}" --no-align --tuples-only --set=ON_ERROR_STOP=1 \
    --command="SELECT count(*) FROM crawl_run;"
)"
decision_count="$(
  psql --dbname="${drill_database}" --no-align --tuples-only --set=ON_ERROR_STOP=1 \
    --command="SELECT count(*) FROM decision_assessment;"
)"
migration_count="$(
  psql --dbname="${drill_database}" --no-align --tuples-only --set=ON_ERROR_STOP=1 \
    --command="SELECT count(*) FROM alembic_version;"
)"

dropdb --if-exists --force "${drill_database}"
created=false

completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
backup_checksum="$(sha256sum "${resolved_backup}" | awk '{print $1}')"
report_path="${backup_directory}/restore_drill_${timestamp}_$$.json"
report_tmp="${report_path}.tmp"
printf '%s\n' \
  '{' \
  '  "schema_version": 1,' \
  '  "status": "success",' \
  "  \"completed_at\": \"${completed_at}\"," \
  "  \"backup_filename\": \"$(basename "${resolved_backup}")\"," \
  "  \"backup_sha256\": \"${backup_checksum}\"," \
  "  \"listing_count\": ${listing_count}," \
  "  \"crawl_run_count\": ${crawl_run_count}," \
  "  \"decision_assessment_count\": ${decision_count}," \
  "  \"migration_row_count\": ${migration_count}," \
  '  "temporary_database_removed": true' \
  '}' >"${report_tmp}"
mv "${report_tmp}" "${report_path}"

echo "restore_drill_succeeded report=${report_path} listings=${listing_count} decisions=${decision_count}"
