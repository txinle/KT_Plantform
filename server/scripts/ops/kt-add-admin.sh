#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
if [ -w /var/run/docker.sock ]; then
    set -- docker
else
    set -- sudo docker
fi
container=$("$@" ps -q --filter label=com.docker.compose.project=kt_plantform --filter label=com.docker.compose.service=web | head -n 1)
if [ -z "$container" ]; then
    echo 'No running KT production web container was found.' >&2
    exit 1
fi
# The command stays outside the image so installation needs no service restart.
source=$(cat "$script_dir/kt-add-admin.py")
exec "$@" exec -it "$container" python manage.py shell -c "$source
Command().handle()"
