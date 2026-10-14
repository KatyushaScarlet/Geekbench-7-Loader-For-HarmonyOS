#!/usr/bin/env python3
# Copyright (C) 2026 hqzing
# SPDX-License-Identifier: 0BSD
# Repository: https://github.com/hqzing/ohos-selfsign
#
# Licensed under the BSD Zero Clause License.

"""
selfsign.py — 轻量级 OpenHarmony 二进制自签名工具

用法:
    python3 selfsign.py <input_elf> [output_elf] [--force] [--strip] [--check]
        缺省 output 时, inplace 改写 input.
        --force : 若已含 .codesign 段, 先剥离再重签
        --strip : 仅剥离 .codesign 段, 不做签名
        --check : 只读校验已有自签名的有效性, 不写任何文件

─────────────────────────────────────────────────────────────
函数划分说明:

一、签名必需的算法核心 (缺一不可, 组成 sign_elf 主流程):
    sha256 / merkle_root_hash / build_descriptor /
    inject_codesign_section / sign_elf

二、ELF 预清洗 / 标准化动作 (并非签名所必需, 是签名前的
    "把 ELF 恢复到干净、可签状态" 的辅助动作, 已独立成函数,
    签名流程只有 --force 时才按需调用):
    parse_elf_header     — 校验并解析 ELF64 header (预检, 只读)
    find_section_by_name — 按名字在 SHT 里找段 (预检, 只读)
    has_codesign_section — 检测是否已含 .codesign 段 (预检, 只读)
    strip_codesign       — 剥离已有 .codesign 段并重建 SHT/shstrtab
                           (标准化; 纯加签模式下不执行)
    check_selfsign       — 只读校验已有自签名的有效性 (--check)

三、文件 I/O 层 (与签名算法无关的健壮性):
    sign_file_atomic     — 临时文件 + rename 原子落盘, 保留原权限位
"""
import hashlib
import os
import struct
import sys

DESC_SIZE = 256
PAGE_SIZE = 4096
FLAG_SELF_SIGN = 0x10
FS_VERITY_DESCRIPTOR_TYPE = 1
HASH_OUT = 32  # SHA-256 输出字节数

# ELF64 header 字段偏移
E_SHOFF, E_SHENTSIZE, E_SHNUM, E_SHSTRNDX = 0x28, 0x3a, 0x3c, 0x3e

CODESIGN_NAME = b".codesign\x00"  # 含结尾 NUL, 共 10 字节


def sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


# ─────────────────────── 字节读写工具 ───────────────────────
def read_u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def read_u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def read_u64(buf: bytes, off: int) -> int:
    return struct.unpack_from("<Q", buf, off)[0]


def write_u16(buf: bytearray, off: int, v: int) -> None:
    struct.pack_into("<H", buf, off, v)


def write_u32(buf: bytearray, off: int, v: int) -> None:
    struct.pack_into("<I", buf, off, v)


def write_u64(buf: bytearray, off: int, v: int) -> None:
    struct.pack_into("<Q", buf, off, v)


def align_up(v: int, a: int) -> int:
    return (v + a - 1) // a * a


# ─────────────────── ELF 预清洗/标准化 (非签名必需) ───────────────────
def parse_elf_header(elf: bytes) -> tuple[int, int, int, int]:
    """校验并解析 ELF64 header (只读预检).

    这不是签名算法的一部分, 而是签名/剥离共用的"先决条件检查":
    - 校验魔数 \\x7fELF + ELFCLASS64
    - 校验 e_shentsize >= 64 (ELF64 允许段表条目大于 64 字节以携带扩展
      字段, 故只拒绝小于 64 的; 实际条目大小随返回值传出, 后续所有
      SHT 遍历/越界校验均按它步进)
    - 校验 SHT 不越界, e_shstrndx < e_shnum

    返回 (e_shoff, e_shentsize, e_shnum, e_shstrndx); 非法输入抛 ValueError.
    """
    if len(elf) < 64 or elf[:4] != b"\x7fELF" or elf[4] != 2:
        raise ValueError("not ELF64")
    e_shoff = read_u64(elf, E_SHOFF)
    e_shentsize = read_u16(elf, E_SHENTSIZE)
    e_shnum = read_u16(elf, E_SHNUM)
    e_shstrndx = read_u16(elf, E_SHSTRNDX)
    if e_shentsize < 64 or e_shoff == 0 or e_shnum == 0 or e_shstrndx >= e_shnum:
        raise ValueError("ELF has no usable section header table")
    if e_shoff > len(elf) or e_shnum > (len(elf) - e_shoff) // e_shentsize:
        raise ValueError("section header table out of bounds")
    return e_shoff, e_shentsize, e_shnum, e_shstrndx


def find_section_by_name(elf: bytes, e_shoff: int, e_shentsize: int,
                         e_shnum: int, e_shstrndx: int, name: bytes) -> int:
    """在 SHT 中按名字找段 (只读预检). 段条目按实际 e_shentsize 步进.

    返回段条目在文件中的偏移 (即 e_shoff + idx*e_shentsize), 未找到返回 -1.
    """
    name_len = len(name)
    shstr_e = e_shoff + e_shstrndx * e_shentsize
    shstr_off = read_u64(elf, shstr_e + 24)
    shstr_sz = read_u64(elf, shstr_e + 32)
    if shstr_off + shstr_sz > len(elf):
        return -1
    for i in range(e_shnum):
        e = e_shoff + i * e_shentsize
        name_off = read_u32(elf, e)
        if name_off + name_len <= shstr_sz:
            if elf[shstr_off + name_off: shstr_off + name_off + name_len] == name:
                return e
    return -1


def has_codesign_section(elf: bytes) -> bool:
    """检测 ELF 是否已含 .codesign 段 (只读预检).

    签名前先问一句"是不是已经签过", 由调用方决定:
    - 纯加签模式: 已含则直接报"已签名"错误
    - --force 模式: 已含则先走 strip_codesign 再签
    """
    try:
        e_shoff, e_shentsize, e_shnum, e_shstrndx = parse_elf_header(elf)
    except ValueError:
        return False
    return find_section_by_name(elf, e_shoff, e_shentsize, e_shnum, e_shstrndx,
                                CODESIGN_NAME) >= 0


def _new_shstrndx(old_shstrndx: int, cs_idx: int) -> int:
    """strip 时 shstrtab 在新 SHT 中的索引: 若 .codesign 在 shstrtab 之前被删,
    shstrtab 的索引整体前移一位, 否则不变."""
    return old_shstrndx - 1 if cs_idx < old_shstrndx else old_shstrndx


def strip_codesign(buf: bytearray) -> tuple[bool, bytearray]:
    """剥离 .codesign 段, 重建 shstrtab 与 SHT (ELF 标准化).

    ⚠ 这是"预清洗/标准化"动作, 并非签名所必需: 签名算法本身只要求输入
    是干净的 ELF64; 若输入已带 .codesign 段, 反复加签会累积畸形段.
    只有 --force 才会调用它.

    剥离步骤:
      1. 定位 .codesign 段条目 → cs_idx
      2. 从 shstrtab 中 drain 掉 ".codesign\\0" (cs_name_off .. +10)
      3. 新 SHT = 旧 SHT 去掉 cs_idx 条目 (条目不压缩, 按原 e_shentsize 保留)
      4. 文件截断到 cs_sec_off (段在文件中的偏移), 其后依次放新 shstrtab、
         8B 对齐的新 SHT
      5. 重写 shstrtab 条目偏移/大小 (注意 cs_idx 在 shstrtab 前后时索引调整)
      6. 所有 sh_name > cs_name_off 的段名偏移整体前移 cs_name_len
      7. 更新 header: e_shoff / e_shnum / e_shstrndx (仅 cs_idx < e_shstrndx 时 -1)

    返回 (removed, new_bytes); removed 为 False 表示本来就没有 .codesign.
    """
    elf = bytes(buf)
    e_shoff, e_shentsize, e_shnum, e_shstrndx = parse_elf_header(elf)

    cs_entry_off = find_section_by_name(elf, e_shoff, e_shentsize, e_shnum,
                                        e_shstrndx, CODESIGN_NAME)
    if cs_entry_off < 0:
        return False, bytearray(elf)
    cs_idx = (cs_entry_off - e_shoff) // e_shentsize

    # 读 shstrtab 位置并做越界检查
    shstr_e = e_shoff + e_shstrndx * e_shentsize
    shstr_off = read_u64(elf, shstr_e + 24)
    shstr_sz = read_u64(elf, shstr_e + 32)
    if shstr_off + shstr_sz > len(elf):
        raise ValueError("shstrtab out of bounds")

    # 2. 新 shstrtab = 旧 shstrtab 删掉 ".codesign\0"
    cs_name_off = read_u32(elf, cs_entry_off)
    cs_name_len = len(CODESIGN_NAME)  # 10, 含 NUL
    new_shstr = bytearray(elf[shstr_off: shstr_off + shstr_sz])
    if cs_name_off + cs_name_len <= shstr_sz:
        del new_shstr[cs_name_off: cs_name_off + cs_name_len]
    new_shstr_sz = len(new_shstr)

    # 3. 新 SHT = 旧 SHT 去掉 cs_idx 条目 (每条目按 e_shentsize 字节原样拷贝)
    new_shnum = e_shnum - 1
    new_sht = bytearray()
    for i in range(e_shnum):
        if i == cs_idx:
            continue
        e = e_shoff + i * e_shentsize
        new_sht += elf[e: e + e_shentsize]

    # 4. 截断到 .codesign 段文件偏移, 依次追加 新shstrtab / 8B对齐 新SHT
    cs_sec_off = read_u64(elf, cs_entry_off + 24)
    keep_len = min(cs_sec_off, len(elf))
    new_shstr_off = keep_len
    new_sht_off = align_up(new_shstr_off + new_shstr_sz, 8)
    new_total = new_sht_off + new_shnum * e_shentsize

    out = bytearray(new_total)
    out[0:keep_len] = elf[0:keep_len]
    out[new_shstr_off: new_shstr_off + new_shstr_sz] = new_shstr
    out[new_sht_off: new_sht_off + new_shnum * e_shentsize] = new_sht

    # 5. 重写 shstrtab 条目: 新 shstr 位置/大小 (cs_idx 在 shstrtab 前后时索引不同)
    shstr_entry_off_in_new = _new_shstrndx(e_shstrndx, cs_idx) * e_shentsize
    write_u64(out, new_sht_off + shstr_entry_off_in_new + 24, new_shstr_off)
    write_u64(out, new_sht_off + shstr_entry_off_in_new + 32, new_shstr_sz)

    # 6. 所有 sh_name > cs_name_off 的段名偏移整体前移 cs_name_len
    for i in range(new_shnum):
        e = new_sht_off + i * e_shentsize
        noff = read_u32(out, e)
        if noff > cs_name_off:
            write_u32(out, e, noff - cs_name_len)

    # 7. 更新 header
    write_u64(out, E_SHOFF, new_sht_off)
    write_u16(out, E_SHNUM, new_shnum)
    if cs_idx < e_shstrndx:
        write_u16(out, E_SHSTRNDX, e_shstrndx - 1)

    return True, out


# ─────────────────── 签名必需的算法核心 ───────────────────
def inject_codesign_section(elf: bytes) -> tuple[bytes, int]:
    """注入 4KB 占位 .codesign 段 (签名第一步).

    这是签名算法的必要组成部分, 流程如下:
      1. 计算所有段末尾的最大值 cur_end = max(e_shoff+e_shnum*e_shentsize,
         各段 off+sz [SHT_NOBITS 不计], 文件实际长度), 段文件偏移
         cs_off = align_up(cur_end, 4096)
      2. 新 shstrtab = 旧 + ".codesign\\0"; 落位 cs_off+4096
      3. 新 SHT 落位新 shstrtab 之后 (8B 对齐), 按原 e_shentsize 复制旧 SHT,
         追加 .codesign 条目 (条目前 64B 是标准字段: sh_type=SHT_PROGBITS,
         sh_offset=cs_off, sh_size=4096, sh_addralign=4096; 超出 64B 的
         扩展部分保持 0)
      4. 更新 shstrtab 条目偏移/大小, 更新 header e_shoff/e_shnum (e_shstrndx 不变)

    关键差异: 只拷贝 [0, min(len(elf), cs_off)) 的原始内容, cs_off 之后到
    新布局之间的区域全部保持 0 — cur_end 已计入文件实际长度, 故段表之后
    拖着的额外字节 (如 Bun/.NET SingleFile 附加数据) 同样进入产物.

    返回 (含段产物, 段文件偏移).
    """
    e_shoff, e_shentsize, e_shnum, e_shstrndx = parse_elf_header(elf)

    # shstrtab entry (用于取旧 shstr 内容)
    shstr_e = e_shoff + e_shstrndx * e_shentsize
    shstr_off = read_u64(elf, shstr_e + 24)
    shstr_sz = read_u64(elf, shstr_e + 32)
    if shstr_off + shstr_sz > len(elf):
        raise ValueError("shstrtab out of bounds")

    # 1. cur_end: SHT 末尾与各段 off+sz 的最大值 (SHT_NOBITS=8 不占文件),
    # 且不小于文件实际长度 (段表之后可能拖着附加数据)
    cur_end = e_shoff + e_shnum * e_shentsize
    for i in range(e_shnum):
        e = e_shoff + i * e_shentsize
        sh_type = read_u32(elf, e + 4)
        off = read_u64(elf, e + 24)
        sz = 0 if sh_type == 8 else read_u64(elf, e + 32)
        if off + sz > cur_end:
            cur_end = off + sz
    if len(elf) > cur_end:
        cur_end = len(elf)
    cs_off = align_up(cur_end, PAGE_SIZE)

    # 2. 新 shstrtab = 旧 + ".codesign\0"
    new_shstr = bytearray(elf[shstr_off: shstr_off + shstr_sz])
    new_shstr += CODESIGN_NAME
    new_shstr_sz = len(new_shstr)
    cs_shname = shstr_sz  # .codesign 在新 shstrtab 内的偏移

    # 3. 新布局: 段在 cs_off, 新 shstrtab 在 cs_off+4096, 新 SHT 8B 对齐之后
    new_shstr_off = cs_off + PAGE_SIZE
    new_sht_off = align_up(new_shstr_off + new_shstr_sz, 8)
    new_shnum = e_shnum + 1
    new_total = new_sht_off + new_shnum * e_shentsize

    buf = bytearray(new_total)
    # 4. 拷贝原内容: 只拷到 cs_off
    copy_len = min(len(elf), new_total, cs_off)
    buf[0:copy_len] = elf[0:copy_len]
    # cs_off..cs_off+4096 为段占位 (bytearray 初始全 0), 稍后写入 payload

    buf[new_shstr_off: new_shstr_off + new_shstr_sz] = new_shstr
    buf[new_sht_off: new_sht_off + e_shnum * e_shentsize] = \
        elf[e_shoff: e_shoff + e_shnum * e_shentsize]

    # .codesign 段条目 (前 64B 为标准字段, 超出 64B 的扩展部分保持 0)
    cs_e = new_sht_off + e_shnum * e_shentsize
    write_u32(buf, cs_e + 0, cs_shname)            # sh_name
    write_u32(buf, cs_e + 4, 1)                    # sh_type = SHT_PROGBITS
    # sh_flags/sh_addr 保持 0
    write_u64(buf, cs_e + 24, cs_off)              # sh_offset
    write_u64(buf, cs_e + 32, PAGE_SIZE)           # sh_size
    # sh_link/sh_info 保持 0
    write_u64(buf, cs_e + 48, PAGE_SIZE)           # sh_addralign
    # sh_entsize 保持 0

    # 更新 shstrtab 条目偏移/大小 (在新 SHT 中的索引仍为 e_shstrndx)
    shstr_e_new = new_sht_off + e_shstrndx * e_shentsize
    write_u64(buf, shstr_e_new + 24, new_shstr_off)
    write_u64(buf, shstr_e_new + 32, new_shstr_sz)

    # 更新 header: e_shoff / e_shnum; e_shstrndx 不变
    write_u64(buf, E_SHOFF, new_sht_off)
    write_u16(buf, E_SHNUM, new_shnum)

    return bytes(buf), cs_off


def merkle_root_hash(data: bytes, cs_off: int, cs_len: int) -> tuple[bytes, bytes]:
    """fs-verity Merkle 树根哈希 + 中间层哈希 (签名必需).

    与上游 merkle_tree_builder.cpp::RunHashTask 等价:
    - 叶层: 每 4096B 一页 SHA-256, 末页零填充; 段所在页
      [cs_off/PAGE, ceil((cs_off+cs_len)/PAGE)) 的叶哈希全置 0
    - 上推: 每页打包 128 个 32B 哈希再 SHA-256, 末页零填充, 直到
      整层 packed <= 4096, 补零后哈希即根

    返回 (根哈希, 中间层): 中间层 = 叶层与根所在层之间的所有层,
    从低往高逐层把 32B 哈希按顺序拼接 (叶层 <= 4096B 时不存在中间层),
    签名时写入 .codesign 段内 payload 之后.
    """
    if len(data) == 0:
        return sha256(bytes(PAGE_SIZE)), b""

    npages = (len(data) + PAGE_SIZE - 1) // PAGE_SIZE
    cs_page_begin = cs_off // PAGE_SIZE
    cs_page_end = (cs_off + cs_len + PAGE_SIZE - 1) // PAGE_SIZE

    hashes = bytearray()
    for i in range(npages):
        if cs_len > 0 and cs_page_begin <= i < cs_page_end:
            hashes += bytes(HASH_OUT)  # 段所在页: 叶哈希置0
            continue
        page = data[i * PAGE_SIZE: (i + 1) * PAGE_SIZE]
        if len(page) < PAGE_SIZE:
            page = page + bytes(PAGE_SIZE - len(page))  # 末页补0
        hashes += sha256(page)

    if npages == 1:
        return bytes(hashes[:HASH_OUT]), b""

    cur = bytes(hashes)
    mid = bytearray()  # 中间层暂存: 低于根所在层的各层, 从低往高拼接
    while len(cur) > PAGE_SIZE:
        nxt = bytearray()
        for i in range(0, len(cur), PAGE_SIZE):
            page = cur[i: i + PAGE_SIZE]
            if len(page) < PAGE_SIZE:
                page = page + bytes(PAGE_SIZE - len(page))
            nxt += sha256(page)
        if len(nxt) > PAGE_SIZE:
            mid += nxt  # 根所在层 (packed <= 4096) 不属于中间层
        cur = bytes(nxt)
    return sha256(cur + bytes(PAGE_SIZE - len(cur))), bytes(mid)


def build_descriptor(sign_size: int, file_size: int, root: bytes,
                     flags: int) -> bytes:
    """构造 256 字节 fs-verity descriptor (签名必需).

    字段布局, 全小端:
      off  size  field
      0    1     version = 1
      1    1     hashAlgorithm = 1 (SHA-256)
      2    1     log2BlockSize = 12 (4096)
      3    1     saltSize = 0
      4    4     signSize (摘要时=0, 落盘时=32)
      8    8     fileSize
      16   64    rootHash (32B 左对齐, 余 0)
      80   32    salt (全 0)
      112  4     flags = FLAG_SELF_SIGN 0x10
      116  4     reserved1 = 0
      120  8     merkleTreeOffset = 0
      128  127   reserved2 = 0
      255  1     csVersion = 3
    """
    d = bytearray(DESC_SIZE)
    d[0] = 1            # version
    d[1] = 1            # hashAlgorithm = SHA-256
    d[2] = 12           # log2BlockSize = 2^12 = 4096
    d[3] = 0            # saltSize
    d[4:8] = sign_size.to_bytes(4, "little")
    d[8:16] = file_size.to_bytes(8, "little")
    d[16:16 + 32] = root            # rootHash 左对齐填 64B, 后 32B 保持 0
    # d[80:112] salt 全 0
    d[112:116] = flags.to_bytes(4, "little")
    # d[116:120] reserved1=0; d[120:128] merkleTreeOffset=0; d[128:255] reserved2=0
    d[255] = 3          # csVersion
    return bytes(d)


def sign_elf(elf: bytes, force: bool) -> bytes:
    """签名主流程 (签名必需): 注入占位段 → merkle → descriptor → signature
    → 拼 payload 写入段内.

    - force == False: 若已含 .codesign 段 → 抛 ValueError ("已签名")
    - force == True : 先 strip_codesign 预清洗, 再签
    """
    if len(elf) < 64 or elf[:4] != b"\x7fELF" or elf[4] != 2:
        raise ValueError("not ELF64")

    # 预处理阶段: 已签名时按 force 决定报错或先清洗
    buf = bytearray(elf)
    if has_codesign_section(buf):
        if not force:
            raise ValueError(
                "already has a .codesign section; strip first or use --force")
        buf = strip_codesign(buf)[1]

    # 1. 注入 4KB 占位 .codesign 段
    tmp, cs_off = inject_codesign_section(bytes(buf))
    file_size = len(tmp)

    # 2. merkle 根哈希: 跳过 [cs_off, cs_off+4096); mid 为中间层哈希
    root, mid = merkle_root_hash(tmp, cs_off, PAGE_SIZE)

    # 3/4. descriptor(signSize=0) 用于摘要
    desc_for_digest = build_descriptor(0, file_size, root, FLAG_SELF_SIGN)
    # 5. signature = SHA256(descriptor)
    signature = sha256(desc_for_digest)
    assert len(signature) == HASH_OUT
    # 6. descriptor(signSize=32) 用于落盘
    desc_on_disk = build_descriptor(32, file_size, root, FLAG_SELF_SIGN)

    # 7. ElfSignInfo: 8B 头 + descriptor 256B + signature 32B = 296B
    payload = bytearray()
    payload += FS_VERITY_DESCRIPTOR_TYPE.to_bytes(4, "little")  # type
    payload += (DESC_SIZE + HASH_OUT).to_bytes(4, "little")     # length = 288
    payload += desc_on_disk
    payload += signature

    # 8. 原地写入段内: payload 之后紧跟中间层哈希, 可用容量
    # 4096-296=3800B, 中间层更长时保留靠叶层一侧的前 3800B
    tmp = bytearray(tmp)
    tmp[cs_off: cs_off + len(payload)] = payload
    tail = mid[:PAGE_SIZE - len(payload)]
    tmp[cs_off + len(payload): cs_off + len(payload) + len(tail)] = tail
    return bytes(tmp)


# ─────────────────── 只读校验 (--check) ───────────────────
def check_selfsign(elf: bytes) -> tuple[bool, str | None]:
    """只读校验已有自签名的有效性 (--check 的实现).

    校验项 (全部通过才算有效; 多项不符时按此顺序报告第一个):
      1. 可解析的 ELF64 (走 parse_elf_header 的全部检查), 存在
         .codesign 段, 且段内 ElfSignInfo 头 type=1 / length=288
      2. descriptor 固定字段完好: version=1, hashAlgorithm=1,
         log2BlockSize=12, csVersion=3
      3. signSize=32 且 fileSize=文件实际大小
      4. 存储的 merkle 根哈希 == 对整个文件重算的根
      5. 存储的 signature == SHA256(signSize 置 0 后的 descriptor)

    明确不校验 merkle 中间层 (payload 296B 之后的区域): 自签名场景内核
    只验根哈希, 且 .codesign 段覆盖的页在 merkle 叶层本来就置 0, 篡改
    中间层不影响根, 此类篡改下本校验仍通过 (预期行为).

    返回 (ok, reason); ok 为 False 时 reason 为原因串 (文案五个实现统一).
    """
    # 1a. ELF64 预检 (parse 失败含段表不可用)
    try:
        e_shoff, e_shentsize, e_shnum, e_shstrndx = parse_elf_header(elf)
    except ValueError:
        return False, "not ELF64"

    # 1b. 定位 .codesign 段, 段内至少要容得下 296B payload
    cs_entry_off = find_section_by_name(elf, e_shoff, e_shentsize, e_shnum,
                                        e_shstrndx, CODESIGN_NAME)
    if cs_entry_off < 0:
        return False, "no .codesign section"
    cs_off = read_u64(elf, cs_entry_off + 24)
    cs_size = read_u64(elf, cs_entry_off + 32)
    payload_len = 8 + DESC_SIZE + HASH_OUT
    if cs_size < payload_len or cs_off + payload_len > len(elf):
        return False, "bad ElfSignInfo header"

    # 1c. ElfSignInfo 头: type=1, length=288
    if (read_u32(elf, cs_off) != FS_VERITY_DESCRIPTOR_TYPE or
            read_u32(elf, cs_off + 4) != DESC_SIZE + HASH_OUT):
        return False, "bad ElfSignInfo header"

    desc = elf[cs_off + 8: cs_off + 8 + DESC_SIZE]
    sig = elf[cs_off + 8 + DESC_SIZE: cs_off + payload_len]

    # 2. descriptor 固定字段: version/hashAlgorithm/log2BlockSize/csVersion
    if desc[0] != 1 or desc[1] != 1 or desc[2] != 12 or desc[255] != 3:
        return False, "unsupported descriptor fields"

    # 3. signSize=32 且 fileSize=文件实际大小
    if read_u32(desc, 4) != HASH_OUT:
        return False, "signSize mismatch"
    if read_u64(desc, 8) != len(elf):
        return False, "fileSize mismatch"

    # 4. merkle 根哈希: 重算整个文件的根 (中间层忽略)
    root, _mid = merkle_root_hash(elf, cs_off, PAGE_SIZE)
    if desc[16:16 + HASH_OUT] != root:
        return False, "merkle root mismatch"

    # 5. signature == SHA256(signSize 置 0 后的 descriptor)
    desc0 = bytearray(desc)
    desc0[4:8] = bytes(4)
    if sig != sha256(bytes(desc0)):
        return False, "signature mismatch"

    return True, None


# ─────────────────── 文件 I/O 层 ───────────────────
def sign_file_atomic(path: str, force: bool) -> None:
    """对文件原子签名 (文件 I/O 层, 与签名算法无关).

    签名结果先写入同目录临时文件
    ("<name>.ohos-signing.<pid>.tmp"), 成功后再 rename 覆盖原文件, 失败
    时删除临时文件, 绝不损坏源文件; 同时保留原文件的权限位 (否则临时文件
    默认 0644, 会剥掉可执行位).
    """
    with open(path, "rb") as f:
        raw = f.read()
    signed = sign_elf(raw, force)

    # 保留原权限位
    mode = None
    try:
        mode = os.stat(path).st_mode & 0o7777
    except OSError:
        pass

    tmp_path = f"{path}.ohos-signing.{os.getpid()}.tmp"
    try:
        os.remove(tmp_path)
    except OSError:
        pass
    with open(tmp_path, "wb") as f:
        f.write(signed)
    if mode is not None:
        os.chmod(tmp_path, mode)
    os.replace(tmp_path, path)


def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def main() -> int:
    force = False
    strip_only = False
    check_only = False
    positional = []
    for a in sys.argv[1:]:
        if a in ("--force", "-f"):
            force = True
        elif a == "--strip":
            strip_only = True
        elif a == "--check":
            check_only = True
        else:
            positional.append(a)
    # --check 与输出路径/--force/--strip 组合属用法错误
    if (len(positional) < 1 or len(positional) > 2 or
            (check_only and (force or strip_only or len(positional) == 2))):
        sys.stderr.write(
            f"usage: {sys.argv[0]} <input_elf> [output_elf] [--force] [--strip] [--check]\n"
            "  (output defaults to input, in-place)\n")
        return 1
    in_path = positional[0]
    out_path = positional[1] if len(positional) == 2 else in_path

    try:
        if check_only:
            # --check: 只读校验, 不写任何文件
            raw = _read_file(in_path)
            ok, reason = check_selfsign(raw)
            if ok:
                print(f"check ok: {in_path}")
                return 0
            sys.stderr.write(f"check failed: {in_path} ({reason})\n")
            return 1

        if strip_only:
            # --strip: 仅预清洗/标准化, 不做签名
            raw = bytearray(_read_file(in_path))
            removed, out = strip_codesign(raw)
            if not removed:
                print(f"no .codesign section to strip: {in_path}")
                return 0
            with open(out_path, "wb") as f:
                f.write(out)
            print(f"strip ok: {in_path} → {out_path} ({len(out)} bytes)")
            return 0

        if in_path == out_path:
            sign_file_atomic(in_path, force)
            print(f"selfsign ok: {in_path} (in-place, {'force' if force else 'append-only'})")
        else:
            raw = _read_file(in_path)
            signed = sign_elf(raw, force)
            with open(out_path, "wb") as f:
                f.write(signed)
            print(f"selfsign ok: {in_path} → {out_path} ({len(signed)} bytes)")
    except ValueError as e:
        sys.stderr.write(f"error: {e}\n")
        return 2
    except OSError as e:
        sys.stderr.write(f"error: {e}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
