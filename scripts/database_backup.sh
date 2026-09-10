#!/usr/bin/env bash
set -Eeuo pipefail

: "${PGHOST:?PGHOST is required}"
: "${PGDATABASE:?PGDATABASE is required}"
: "${PGUSER:?PGUSER is required}"

backup_directory="${SHR_BACKUP_DIRECTORY:-/backups}"
retention_days="${SHR_BACKUP_RETENTION_DAYS:-14}"

if [[ -z "${backup_directory}" || "${backup_directory}" == "/" ]]; then
  echo "unsafe backup directory" >&2
  exit 2
fi
if [[ ! "${PGDATABASE}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  echo "PGDATABASE must be a simple PostgreSQL identifier" >&2
  exit 2
fi
if [[ ! "${retention_days}" =~ ^[0-9]+$ ]] || (( retention_days < 1 )); then
  echo "SHR_BACKUP_RETENTION_DAYS must be a positive integer" >&2
  exit 2
fi

umask 077
mkdir -p "${backup_directory}"

created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
basename="${PGDATABASE}_${timestamp}"
dump_path="${backup_directory}/${basename}.dump"
checksum_path="${dump_path}.sha256"
manifest_path="${dump_path}.manifest.json"
dump_tmp="${backup_directory}/.${basename}.dump.tmp"
checksum_tmp="${backup_directory}/.${basename}.dump.sha256.tmp"
manifest_tmp="${backup_directory}/.${basename}.dump.manifest.json.tmp"

cleanup_temporary_files() {
  rm -f -- "${dump_tmp}" "${checksum_tmp}" "${manifest_tmp}"
}
trap cleanup_temporary_files EXIT

if [[ -e "${dump_path}" ]]; then
  echo "backup already exists: ${dump_path}" >&2
  exit 2
fi

pg_dump \
  --format=custom \
  --compress=9 \
  --no-owner \
  --no-acl \
  --file="${dump_tmp}" \
  "${PGDATABASE}"

pg_restore --list "${dump_tmp}" >/dev/null

checksum="$(sha256sum "${dump_tmp}" | awk '{print $1}')"
size_bytes="$(stat -c %s "${dump_tmp}")"
printf '%s  %s\n' "${checksum}" "$(basename "${dump_path}")" >"${checksum_tmp}"
printf '%s\n' \
  '{' \
  '  "schema_version": 1,' \
  '  "status": "verified",' \
  "  \"database\": \"${PGDATABASE}\"," \
  "  \"created_at\": \"${created_at}\"," \
  "  \"filename\": \"$(basename "${dump_path}")\"," \
  '  "format": "postgresql-custom",' \
  "  \"size_bytes\": ${size_bytes}," \
  "  \"sha256\": \"${checksum}\"" \
  '}' >"${manifest_tmp}"

mv "${dump_tmp}" "${dump_path}"
mv "${checksum_tmp}" "${checksum_path}"
mv "${manifest_tmp}" "${manifest_path}"
ln -sfn "$(basename "${dump_path}")" "${backup_directory}/latest.dump"
ln -sfn "$(basename "${checksum_path}")" "${backup_directory}/latest.dump.sha256"
ln -sfn "$(basename "${manifest_path}")" "${backup_directory}/latest.dump.manifest.json"

find "${backup_directory}" -maxdepth 1 -type f \
  -name "${PGDATABASE}_*.dump" -mtime "+${retention_days}" -delete
find "${backup_directory}" -maxdepth 1 -type f \
  -name "${PGDATABASE}_*.dump.sha256" -mtime "+${retention_days}" -delete
find "${backup_directory}" -maxdepth 1 -type f \
  -name "${PGDATABASE}_*.dump.manifest.json" -mtime "+${retention_days}" -delete

echo "backup_created path=${dump_path} size_bytes=${size_bytes} sha256=${checksum}"
