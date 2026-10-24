# sourced by the start/stop scripts. Defines: machines_of <app>, act <start|stop> <app>, run, confirm.
# DRY_RUN=1 prints the fly commands instead of running them.
command -v fly >/dev/null 2>&1 || { echo "fly CLI not found" >&2; exit 1; }

run() { if [ -n "${DRY_RUN:-}" ]; then echo "[dry run] $*"; else "$@"; fi; }

machines_of() {
  local listing
  listing="$(fly machine list --app "$1" --json)" || { echo "could not list the machines of app $1" >&2; exit 1; }
  local ids
  ids="$(printf '%s' "$listing" | python3 -c 'import json,sys; print(" ".join(m["id"] for m in json.load(sys.stdin)))')"
  [ -n "$ids" ] || { echo "no machines found in app $1" >&2; exit 1; }
  printf '%s' "$ids"
}

# act start|stop <app>: runs it on every Machine of the app, then shows the status
act() {
  local verb="$1" app="$2" id
  for id in $(machines_of "$app"); do run fly machine "$verb" "$id" --app "$app"; done
  run fly status --app "$app"
}

# confirm "<question>": asks y/N unless YES=1 or DRY_RUN is set
confirm() {
  [ -n "${YES:-}" ] || [ -n "${DRY_RUN:-}" ] && return 0
  read -r -p "$1 [y/N] " answer
  case "$answer" in y|Y|yes|YES) return 0 ;; *) echo "cancelled"; exit 1 ;; esac
}
