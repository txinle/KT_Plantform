#!/bin/sh
set -eu
source_dir="${1:-/opt/kt-frontend-assets}"
target_dir="${2:-/static/spa-assets/assets}"
# Hashed chunks stay available to already-open pages across release and rollback.
mkdir -p "$target_dir"
cp -R "$source_dir"/. "$target_dir"/
shell_source="${3:-/opt/kt-frontend-shells}"
if [ -d "$shell_source" ]; then
    mkdir -p "$target_dir/shells"
    cp "$shell_source"/capture-shell-*.html "$target_dir/shells/"
fi
# Offline archives can retain owner-only modes. Only the public frontend tree
# receives readable files and traversable directories; private inputs stay private.
public_root="$(dirname "$target_dir")"
find "$public_root" -type d -exec chmod 755 {} +
find "$public_root" -type f -exec chmod 644 {} +
