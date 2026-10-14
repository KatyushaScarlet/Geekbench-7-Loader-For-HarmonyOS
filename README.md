# Geekbench 7 Loader For HarmonyOS

本项目提供一个 HarmonyOS 加载器，用于运行 Geekbench 7.1.0 的 Linux/AArch64 版本。

[运行原理与完整加载过程](PRINCIPLES.md)

## 文件

```text
src/           loader.c：OpenHarmony 原生入口源码
glibc/         GNU 加载器映射、线程适配及 TRACE 控制
scripts/       Docker glibc 构建和 SDK loader 编译脚本
packages/      GNU 源码归档与库包
dist/          加载器、库、自签名工具和启动脚本
```

## 1. 构建 glibc

Linux x86_64 PC 安装并启动 Docker，从仓库根目录执行：

```sh
bash scripts/build-glibc.sh
```

脚本创建 Ubuntu 24.04 构建镜像，安装 GNU AArch64 交叉 gcc/g++、目标头文件、make、gawk、bison、texinfo、Python、dpkg-dev 和 patch，然后编译适配 glibc。源码解压和编译在容器临时目录完成，产物直接写入 `dist/`。

## 2. 编译 loader

> 本仓库的 `dist/` 已提供编译好的加载器和运行库。直接使用时，可跳转到 [放入 Geekbench](#3-放入-geekbench)，然后在 HarmonyOS PC 上签名与运行。

下载 OpenHarmony 7.x Linux SDK：

https://repo.huaweicloud.com/openharmony/os/7.0-Release/ohos-sdk-windows_linux-public_20260829.tar.gz

校验文件为同一地址加 `.sha256`。展开归档中的 Linux native 组件 zip，解压后得到包含 `llvm/bin/clang` 和 `sysroot/` 的 `native` 目录。将其绝对路径填入 `OHOS_NATIVE_HOME`：

```sh
export OHOS_NATIVE_HOME=/absolute/path/to/sdk/native
bash scripts/build-loader.sh
```

`src/loader.c` 编译为 `dist/loader`。

## 3. 放入 Geekbench

从 https://www.geekbench.com/preview/ 获取 Geekbench 7.1.0 
Linux/AArch64 安装包，在 PC 上解压。

下载地址：

https://geekbench-cdn.b-cdn.net/Geekbench-7.1.0-LinuxARMPreview.tar.gz

将包内以下文件直接复制到 `dist/`：

```text
geekbench_aarch64
geekbench.plxr
geekbench-workload.plxr
```

本仓库发布内容仅包含加载器、配套运行库和工具，Geekbench 7.1.0 程序及资源由使用者自行取得。

## 4. 在 HarmonyOS PC 签名与运行

首次运行前，在 HarmonyOS PC 上进入 **设置 > 隐私和安全 > 高级**，打开 **运行外部来源的扩展程序** 开关。该开关适用于签名后的外部二进制文件和动态库（如 `bin`、`.so`），不包含鸿蒙应用。参见[华为官方说明：华为鸿蒙电脑运行外部来源的扩展程序功能介绍](https://consumer.huawei.com/cn/support/content/zh-cn16079826/)。

将完整 `dist/` 复制到 HarmonyOS PC，在设备终端进入该目录：

```sh
# 签名目录下的二进制文件
sh sign.sh

# 运行 Geekbench
sh run.sh
```

`sign.sh` 使用目录内的社区自签名工具处理入口、GNU loader、Geekbench 和共享库，Python 需要 3.11 或更新版本。`run.sh` 设置工作目录并启动计算主程序。Preview 运行需要联网并自动上传结果。

开启线程诊断输出：

```sh
./loader --trace --linker ./ld-linux-aarch64.so.1 --library-path ./lib --argv0 geekbench_aarch64 -- ./geekbench_aarch64
```

## 部署布局

```text
dist/
  loader
  ld-linux-aarch64.so.1
  lib/libc.so.6
  lib/libm.so.6
  lib/libgcc_s.so.1
  selfsign.py
  selfsign.LICENSE
  sign.sh
  run.sh
  geekbench_aarch64         用户放入
  geekbench.plxr            用户放入
  geekbench-workload.plxr   用户放入
```

## GNU 包及源码获取

原始归档保存在 `packages/`，SHA256 见 `SHA256SUMS`：

- glibc 源码：https://archive.ubuntu.com/ubuntu/pool/main/g/glibc/glibc_2.39.orig.tar.xz
- Ubuntu 补丁：https://archive.ubuntu.com/ubuntu/pool/main/g/glibc/glibc_2.39-0ubuntu8.debian.tar.xz
- 源包描述：https://archive.ubuntu.com/ubuntu/pool/main/g/glibc/glibc_2.39-0ubuntu8.dsc
- libgcc 包：https://archive.ubuntu.com/ubuntu/pool/main/g/gcc-14-cross/libgcc-s1-arm64-cross_14.2.0-4ubuntu2~24.04.1cross1_all.deb
- GNU 原库包：https://archive.ubuntu.com/ubuntu/pool/main/c/cross-toolchain-base/libc6-arm64-cross_2.39-0ubuntu8cross1_all.deb

`dist/` 的 GNU loader、libc、libm 由带项目补丁的 glibc 源码编译。libgcc_s 从上述 deb 的 `usr/aarch64-linux-gnu/lib/libgcc_s.so.1` 提取。

自签名源码：https://github.com/hqzing/ohos-bst-light ，固定版本 `eee7eaf7ce18d6f5a543ac023df1d8b2a4a186a1`，许可见 `dist/selfsign.LICENSE`。

## 许可

项目源码和构建脚本使用 MIT（LICENSE），签名工具使用 0BSD，glibc 使用 LGPL（glibc/COPYING.LIB）。GNU 源包及库包中的版权文件保留其原始许可。
