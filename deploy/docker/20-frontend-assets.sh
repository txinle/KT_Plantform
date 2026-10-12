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
