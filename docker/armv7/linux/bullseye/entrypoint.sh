#!/bin/bash

# Fail on errors.
set -e

echo "$@"

if [[ "$@" == "" ]]; then
    pyinstaller /src/omblepy.spec --clean --distpath /src/dist
    chown -R --reference=/src /src/dist
else
    sh -c "$@"
fi
