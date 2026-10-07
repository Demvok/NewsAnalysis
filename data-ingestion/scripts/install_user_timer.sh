#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

for unit in s-scope-ingest.service s-scope-ingest.timer; do
  source="$PROJECT_DIR/deploy/systemd/$unit"
  target="$UNIT_DIR/$unit"
  if [[ -e "$target" && ! -L "$target" ]]; then
    echo "Refusing to replace existing non-symlink unit: $target" >&2
    exit 1
  fi
  ln -sfn "$source" "$target"
done

systemctl --user daemon-reload
systemctl --user enable --now s-scope-ingest.timer
systemctl --user list-timers s-scope-ingest.timer --no-pager
