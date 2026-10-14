#define _XOPEN_SOURCE 700
#include <limits.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv)
{
    const char *linker = NULL, *library = NULL, *argv0 = NULL;
    int trace = 0, target = -1;
    for (int i=1; i<argc; ++i) {
        if (!strcmp(argv[i], "--trace")) trace = 1;
        else if (!strcmp(argv[i], "--linker") && i+1<argc) linker = argv[++i];
        else if (!strcmp(argv[i], "--library-path") && i+1<argc) library = argv[++i];
        else if (!strcmp(argv[i], "--argv0") && i+1<argc) argv0 = argv[++i];
        else if (!strcmp(argv[i], "--help")) goto usage;
        else if (!strcmp(argv[i], "--") && i+1<argc) { target = i+1; break; }
        else goto usage;
    }
    if (!linker || !library || !argv0 || target < 0) goto usage;
    char linker_path[PATH_MAX], library_path[PATH_MAX], program_path[PATH_MAX];
    if (!realpath(linker, linker_path) || !realpath(library, library_path)
        || !realpath(argv[target], program_path)) {
        perror("resolve path"); return 2;
    }
    char **args = calloc((size_t)(argc-target)+7, sizeof(*args));
    if (!args) return 2;
    args[0] = linker_path;
    args[1] = "--inhibit-cache";
    args[2] = "--library-path";
    args[3] = library_path;
    args[4] = "--argv0";
    args[5] = (char *)argv0;
    args[6] = program_path;
    for (int i=target+1; i<argc; ++i) args[i-target+6] = argv[i];
    setenv("GB_HARMONY_TRACE", trace ? "1" : "0", 1);
    setenv("GLIBC_TUNABLES", "glibc.pthread.rseq=0", 1);
    unsetenv("LD_PRELOAD"); unsetenv("LD_LIBRARY_PATH"); unsetenv("LD_AUDIT");
    execv(linker_path, args);
    int error = errno;
    perror("exec GNU linker");
    free(args);
    return error == ENOENT ? 127 : 126;
usage:
    fprintf(stdout, "Usage: %s [--trace] --linker PATH --library-path DIRECTORY --argv0 STRING -- PROGRAM [ARGS...]\n", argv[0]);
    return argc == 1 || (argc == 2 && !strcmp(argv[1], "--help")) ? 0 : 2;
}
