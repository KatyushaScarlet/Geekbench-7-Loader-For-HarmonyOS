#!/usr/bin/env bash
# 从仓库根目录执行。
set -euo pipefail
if [ -z "${OHOS_NATIVE_HOME:-}" ]; then
    printf '请设置 OHOS_NATIVE_HOME，指向 SDK 的 native 目录。\n' >&2
    exit 1
fi
mkdir -p dist
repository_directory="$PWD"
"$OHOS_NATIVE_HOME/llvm/bin/clang" \
    --target=aarch64-linux-ohos \
    --sysroot="$OHOS_NATIVE_HOME/sysroot" \
    -fuse-ld=lld \
    -O2 -Wall -Wextra -Werror \
    "$repository_directory/src/loader.c" \
    -o "$repository_directory/dist/loader"
chmod 755 dist/loader
printf '构建完成：dist/loader\n'
