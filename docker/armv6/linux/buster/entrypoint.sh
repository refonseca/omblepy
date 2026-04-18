#!/bin/bash -i

# Fail on errors.
set -e

cd /src

echo "$@"

if [[ "$@" == "" ]]; then
    pyinstaller --clean -y --dist ./dist/armv7l --workpath /tmp *.spec
    chown -R --reference=. ./dist/armv7l
else
    sh -c "$@"
fi # [[ "$@" == "" ]]

