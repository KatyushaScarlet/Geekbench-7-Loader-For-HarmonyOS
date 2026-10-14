#!/usr/bin/env bash
# Ubuntu 构建容器内执行。
set -euo pipefail

repository_directory="$PWD"
cross_cc="${GLIBC_CC:-aarch64-linux-gnu-gcc}"
cross_cxx="${GLIBC_CXX:-aarch64-linux-gnu-g++}"
kernel_headers="${GLIBC_HEADERS:-/usr/aarch64-linux-gnu/include}"

if ! command -v "$cross_cc" >/dev/null 2>&1; then
    printf '缺少 GNU AArch64 编译器：%s。安装交叉工具链或设置 GLIBC_CC。\n' "$cross_cc" >&2
    exit 1
fi
if ! command -v "$cross_cxx" >/dev/null 2>&1; then
    printf '缺少 GNU AArch64 C++ 编译器：%s。安装交叉工具链或设置 GLIBC_CXX。\n' "$cross_cxx" >&2
    exit 1
fi
if [ ! -f "$kernel_headers/linux/version.h" ]; then
    printf '缺少目标 Linux 头文件：%s/linux/version.h。安装目标开发头文件或设置 GLIBC_HEADERS。\n' "$kernel_headers" >&2
    exit 1
fi
temporary_directory=$(mktemp -d)
trap 'rm -rf "$temporary_directory"' EXIT
source_directory="$temporary_directory/glibc-source"
build_directory="$temporary_directory/glibc-build"
mkdir -p dist/lib

dpkg-source --no-check -x packages/glibc_2.39-0ubuntu8.dsc "$source_directory"
patch -d "$source_directory" -p1 < glibc/glibc-segment-map.patch
patch -d "$source_directory" -p1 < glibc/glibc-legacy-clone.patch
python3 glibc/instrument-threads.py "$source_directory"
python3 glibc/quiet-trace.py "$source_directory"

mkdir -p "$build_directory"
cd "$build_directory"

"$source_directory/configure" \
        --build=x86_64-linux-gnu \
        --host=aarch64-linux-gnu \
        --prefix=/usr \
        --with-headers="$kernel_headers" \
        --disable-werror \
        CC="$cross_cc" \
        CXX="$cross_cxx"

make -j"${JOBS:-8}"
cp elf/ld.so "$repository_directory/dist/ld-linux-aarch64.so.1"
cp libc.so "$repository_directory/dist/lib/libc.so.6"
cp math/libm.so "$repository_directory/dist/lib/libm.so.6"

cd "$repository_directory"
dpkg-deb -x packages/libgcc-s1-arm64-cross_14.2.0-4ubuntu2~24.04.1cross1_all.deb "$temporary_directory/libgcc"
cp "$temporary_directory/libgcc/usr/aarch64-linux-gnu/lib/libgcc_s.so.1" dist/lib/libgcc_s.so.1
