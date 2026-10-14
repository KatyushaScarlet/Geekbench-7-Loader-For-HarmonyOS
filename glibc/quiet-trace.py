#!/usr/bin/env python3
from pathlib import Path
import sys
root = Path(sys.argv[1])
for relative in ('nptl/pthread_create.c', 'sysdeps/unix/sysv/linux/clone-internal.c'):
    path = root/relative
    text = path.read_text()
    if '#include <stdlib.h>' not in text:
        text = '#include <stdlib.h>\n'+text
    lines = text.splitlines()
    out = []
    for line in lines:
        if 'INTERNAL_SYSCALL_CALL (write, 2, "TRACE ' in line:
            out.append('  if (getenv ("GB_HARMONY_TRACE") != NULL')
            out.append('      && getenv ("GB_HARMONY_TRACE")[0] == \'1\')')
            out.append('    '+line.lstrip())
        else:
            out.append(line)
    path.write_text('\n'.join(out)+'\n')
