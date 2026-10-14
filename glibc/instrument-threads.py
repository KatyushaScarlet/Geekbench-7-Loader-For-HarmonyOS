#!/usr/bin/env python3
"""Diagnostic-only markers. No syscall is disabled or replaced."""
from pathlib import Path
import sys

root = Path(sys.argv[1])
def add(path, anchor, label):
    p = root/path
    text = p.read_text()
    if text.count(anchor) != 1:
        raise RuntimeError((path, anchor, text.count(anchor)))
    marker = f'  INTERNAL_SYSCALL_CALL (write, 2, "TRACE {label}\\n", {len("TRACE "+label)+1});\n'
    p.write_text(text.replace(anchor, marker+anchor))

add('nptl/pthread_create.c', '  int err = allocate_stack (iattr, &pd, &stackaddr, &stacksize);', 'before allocate_stack')
add('sysdeps/unix/sysv/linux/clone-internal.c', '      int ret = __clone3 (cl_args, sizeof (*cl_args), func, arg);', 'before clone3 syscall435')
add('sysdeps/unix/sysv/linux/clone-internal.c', '  ret = __clone (func, stack, flags, arg,', 'before clone syscall220')
add('nptl/pthread_create.c', '      INTERNAL_SYSCALL_CALL (set_robust_list, &pd->robust_head,', 'worker before set_robust_list')
