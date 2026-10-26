# sourced by the fly-db-* scripts (after _fly.sh). Talks to Postgres in the `db` container of the Fly Machine over `fly ssh`.
#   psql_in <database> [psql args...]   runs psql in the db container; SQL comes from STDIN (fly ssh passes stdin through)
#   row_counts <database>               "table|rows" for every table in schema public, sorted
#   dump_ok <file>                      true when the file is a COMPLETE plain pg_dump (the last line pg_dump writes is there)
#   pg_dump_to_stdout                   pg_dump of $DB_NAME, plain SQL on stdout
# TARGET=local runs the same commands against the LOCAL docker compose Postgres instead (to rehearse a restore with
#   DB_NAME=<scratch db>; never point it at your dev database unless you mean it). Default TARGET=fly.
APP="${APP:-livebingo}"
DB_USER="${DB_USER:-livegames}"
DB_NAME="${DB_NAME:-livegames}"
TARGET="${TARGET:-fly}"

_in_db_container() {      # _in_db_container "<command line>"  (stdin passed through)
  if [ "$TARGET" = "local" ]; then
    docker compose exec -T db sh -c "$1"
  else
    fly ssh console --app "$APP" --container db -C "sh -c '$1'" 2> >(grep -v '^Connecting to' >&2)
  fi
}

psql_in() {
  local db="$1"; shift
  _in_db_container "psql -U $DB_USER -h 127.0.0.1 -d $db -v ON_ERROR_STOP=1 -X -q $* -f -"
}

pg_dump_to_stdout() {
  _in_db_container "pg_dump -U $DB_USER -d $DB_NAME -h 127.0.0.1 --no-owner"
}

row_counts() {
  printf '%s\n' "SELECT table_name || '|' || (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM public.%I', table_name), false, true, '')))[1]::text
                 FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name;" \
    | psql_in "$1" -At
}

dump_ok() {
  [ -s "$1" ] && grep -q -- '-- PostgreSQL database dump complete' "$1" && grep -q '^COPY ' "$1"
}
