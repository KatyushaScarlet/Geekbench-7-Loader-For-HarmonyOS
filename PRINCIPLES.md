# 运行原理与完整加载过程

本项目提供一个 HarmonyOS 加载器，用于运行 Geekbench 7.1.0 的 Linux/AArch64 版本。它通过 OpenHarmony 原生入口启动私有 GNU 动态加载器，并使用配套 glibc 提供程序需要的 GNU ABI。CPU 指令由设备处理器直接执行。

## 1. 文件与职责

| 文件 | 职责 |
|---|---|
| `run.sh` | 设置工作目录，将明确的加载参数交给 `loader` |
| `loader` | 由 `src/loader.c` 编译的 OpenHarmony 原生入口，解析参数、设置环境并执行 GNU 加载器 |
| `ld-linux-aarch64.so.1` | GNU 动态加载器，映射 ELF、加载依赖、完成重定位和启动初始化 |
| `geekbench_aarch64` | 原版 Geekbench 7.1.0 Linux/AArch64 计算主程序 |
| `geekbench7` | 原安装包提供的启动器，负责选择和启动计算主程序 |
| `lib/libc.so.6` | glibc 的 C 接口、线程、TLS、文件、内存及系统调用封装 |
| `lib/libm.so.6` | GNU 数学函数库 |
| `lib/libgcc_s.so.1` | GCC 运行支持和异常展开接口 |
| `geekbench.plxr`、`geekbench-workload.plxr` | 与 Linux 程序配套的应用及 workload 资源 |
| `sign.sh`、`selfsign.py` | 为程序和共享库生成设备使用的 ELF 自签名 |

`run.sh` 启动的是 `geekbench_aarch64`。该程序在无参数情况下选择 CPU 测试。

## 2. 两个运行库阶段

整个启动包含两个地址空间阶段：

```text
设备终端
  └─ sh run.sh                         系统 shell / musl
       └─ loader                       OpenHarmony 原生程序 / musl
            └─ execv GNU 加载器        替换当前进程地址空间
                 ├─ geekbench_aarch64
                 ├─ libc.so.6          私有 GNU 运行库
                 ├─ libm.so.6
                 └─ libgcc_s.so.1
                      └─ CPU workload  AArch64 处理器直接执行
```

`loader` 由 OpenHarmony SDK 编译，其 ELF 解释器是设备的 `/lib/ld-musl-aarch64.so.1`。进入它的 `main` 时，系统运行库已经完成原生程序初始化。

随后 `execv` 将当前进程切换为 GNU 加载器。内核建立新程序映射、初始栈和辅助向量，GNU 加载器接管目标 ELF 的初始化。切换后，目标程序的 C/C++ 运行需求由私有 GNU 运行库提供。

`execv` 成功后保持进程 PID，替换旧的用户地址空间；线程和后续分配器状态按新程序建立。因此 OpenHarmony 原生入口与 GNU 程序之间的接口是启动参数、环境、文件描述符和进程退出状态。

## 3. `run.sh` 的每一步

脚本内容：

```sh
#!/bin/sh
set -e
cd "$(dirname "$0")"
./loader --linker ./ld-linux-aarch64.so.1 --library-path ./lib --argv0 geekbench_aarch64 -- ./geekbench_aarch64 "$@"
```

1. `#!/bin/sh` 指定使用设备 shell 解释脚本。
2. `set -e` 让脚本在启动前的必要操作出错时结束。
3. `cd` 将当前工作目录设为脚本所在目录，使相对路径与部署文件对应。
4. 最后一行明确指定 GNU 加载器、库目录、目标 `argv[0]` 和目标程序；`"$@"` 将用户给脚本的参数继续传给 Geekbench。

GNU 加载器和 PLXR 文件放在同一个部署目录。显式执行 GNU 加载器时，进程的 `/proc/self/exe` 标识该加载器，这个布局也满足目标按可执行文件所在目录查找资源的行为。

## 4. 原生 `loader` 如何组织参数

源码位于 `src/loader.c`，编译产物名称为 `loader`。

它接收以下接口：

```text
loader [--trace] --linker PATH --library-path DIRECTORY --argv0 STRING -- PROGRAM [ARGS...]
```

| 参数 | 含义 |
|---|---|
| `--linker` | 本次执行的 GNU 加载器路径 |
| `--library-path` | 本次执行使用的私有库目录 |
| `--argv0` | GNU 加载器交给目标程序的 `argv[0]` |
| `--` | 结束入口参数解析，后面是目标程序及其参数 |
| `--trace` | 开启适配运行库中的线程诊断输出 |

`realpath` 将三个路径解析为绝对路径。之后它构造 GNU 加载器的参数数组：

```text
argv[0] = GNU 加载器绝对路径
argv[1] = --inhibit-cache
argv[2] = --library-path
argv[3] = 私有库目录绝对路径
argv[4] = --argv0
argv[5] = 用户指定的目标 argv[0]
argv[6] = 目标程序绝对路径
argv[7...] = 目标程序参数
```

`--inhibit-cache` 关闭 GNU 系统库缓存的使用，`--library-path` 为本次运行提供指定的库搜索目录。具体依赖的最终解析由 GNU 加载器根据 ELF 动态信息完成。

入口还设置：

- `GLIBC_TUNABLES=glibc.pthread.rseq=0`：关闭 glibc 的可选 rseq 注册。
- `GB_HARMONY_TRACE=0`：默认静默；出现入口参数 `--trace` 时设为 `1`。
- 清除 `LD_PRELOAD`、`LD_LIBRARY_PATH`、`LD_AUDIT`：使本次运行使用入口明确提供的加载配置。

无参数或 `--help` 时，入口输出自身帮助。目标程序的选项置于 `-- PROGRAM` 后，按原顺序传递。

## 5. ELF 映射与地址空间布局

GNU 加载器读取主程序 ELF 头、程序头和动态段，取得以下信息：

- `PT_LOAD`：文件段的偏移、虚拟地址、长度、对齐和 R/W/X 权限。
- `PT_DYNAMIC`：依赖库、符号表、重定位表和初始化入口。
- `PT_TLS`：线程局部存储的初始化数据、内存大小和对齐。
- `PT_GNU_RELRO`：重定位结束后可改为只读的区域。

本项目的 `glibc-segment-map.patch` 修改 ET_DYN 映射策略：

1. 为完整 ELF 地址跨度申请匿名 `PROT_NONE` 区域。
2. 按要求对齐装载基址，释放两侧多余区域。
3. 为每个 `PT_LOAD` 使用其自身的文件偏移和原始权限建立映射。
4. 保持段之间空隙不可访问。
5. 初始化尾部零填充和 BSS 区域。

这样每次文件映射的权限都对应实际装载段，代码段使用 RX，数据段使用 RW。段的相对位置保持 ELF 描述的布局，GNU 重定位与 TLS 计算继续使用这一布局。

## 6. 依赖加载、重定位与初始化

主程序的直接 GNU 依赖由私有目录提供。GNU 加载器读取各库的动态符号、版本要求及重定位记录，为引用建立实际地址。

加载过程中完成的主要操作包括：

1. 解析 `DT_NEEDED` 依赖并建立各对象的 link map。
2. 核对 GNU 符号版本，解析程序与共享库的导入符号。
3. 处理地址重定位及 TLS 相关引用。
4. 设置初始线程的线程指针、TLS 和运行库内部状态。
5. 对需要保护的 RELRO 区域调整权限。
6. 调用运行库、程序的初始化函数。
7. 将控制权交给目标 ELF 入口，随后进入程序启动代码和 `main`。

程序后续调用 `dlopen` 时，同一 GNU 加载器继续负责新增共享对象的映射、重定位及初始化。

## 7. 线程运行路径

glibc 的 pthread 实现管理线程栈、线程控制块、独立 TLS、信号掩码以及线程回收。

本项目的 `glibc-legacy-clone.patch` 让内部线程创建使用 glibc 已有的 legacy `clone` 参数转换路径。其工作流程是：

1. 分配线程栈及 TLS。
2. 按共享内存、文件描述符、信号处理等线程属性组织 clone flags。
3. 将 TLS 指针、父线程 TID 地址和线程退出通知地址交给内核。
4. 新线程进入 GNU 启动函数，完成线程局部初始化，再调用用户 worker。
5. worker 返回后执行线程局部析构、记录结果并退出。
6. `pthread_join` 使用运行库同步机制等待退出并取得结果。

`GB_HARMONY_TRACE` 控制关键线程路径的诊断标记。正常启动时值为 `0`；入口指定 `--trace` 时值为 `1`。诊断文本通过运行库中的输出标记产生。

## 8. ELF 自签名过程

`sign.sh` 依次处理部署目录中的原生入口、GNU loader、目标程序和共享库。

社区 `selfsign.py` 为 ELF 建立 `.codesign` 段，其中包含自签名描述、文件长度、Merkle 根哈希以及相应摘要。该段按 4KB 对齐。脚本随后使用 `--check` 重算并核对自签名数据，并为可执行文件设置执行位。

设备在执行程序或映射代码时，根据自身代码验证机制检查这些签名信息。构建、复制和 ELF 参数调整完成后进行签名，可以让签名覆盖最终使用的文件内容。

## 9. 从初始化到 CPU workload

进入 Geekbench 7.1.0 主程序后，程序读取配套 PLXR、识别处理器和内存信息、选择计算实现并创建所需线程。计算指令、内嵌第三方库及 workload 调度代码继续来自原版主程序。

外部 C 接口由私有 glibc、libm 和 libgcc_s 提供，文件与线程等操作经运行库系统调用封装交给设备内核。测试结束时，程序完成结果处理、资源释放和退出，终端取得最终退出状态。

## 10. 对应源码

| 实现 | 位置 |
|---|---|
| OpenHarmony 原生参数入口 | `src/loader.c` |
| 可读启动脚本 | `dist/run.sh` |
| 原生入口与部署包构建 | `scripts/build-loader.sh` |
| GNU 运行库源码构建 | `scripts/build-glibc.sh` |
| ELF 逐段映射 | `glibc/glibc-segment-map.patch` |
| legacy clone 线程路径 | `glibc/glibc-legacy-clone.patch` |
| TRACE 插入及条件控制 | `glibc/instrument-threads.py`、`glibc/quiet-trace.py` |
| 自签名实现及批处理 | `dist/selfsign.py`、`dist/sign.sh` |
| 配套 GNU 源码与库包 | `packages/` |
