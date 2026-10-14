#!/bin/sh
set -eu
cd "$(dirname "$0")"
for file in loader geekbench_aarch64 ld-linux-aarch64.so.1 lib/libc.so.6 lib/libm.so.6 lib/libgcc_s.so.1; do
    python3 selfsign.py "$file" --force
    python3 selfsign.py "$file" --check
    chmod 755 "$file"
done
if [ -f geekbench7 ]; then
    python3 selfsign.py geekbench7 --force
    python3 selfsign.py geekbench7 --check
    chmod 755 geekbench7
fi
