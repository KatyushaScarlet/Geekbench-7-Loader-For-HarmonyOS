#!/bin/sh
set -e
cd "$(dirname "$0")"
./loader --linker ./ld-linux-aarch64.so.1 --library-path ./lib --argv0 geekbench_aarch64 -- ./geekbench_aarch64 "$@"
