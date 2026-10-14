#!/usr/bin/env bash
# 从仓库根目录执行。
set -euo pipefail
docker build -f scripts/Dockerfile.glibc -t harmonyos-glibc-build:2.39 .
repository_directory="$PWD"
docker run --rm \
    --user "$(id -u):$(id -g)" \
    --volume "$repository_directory:/work" \
    --workdir /work \
    harmonyos-glibc-build:2.39 \
    bash scripts/build-glibc-inside.sh
