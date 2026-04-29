/*
 * shield_dump — locate the unpacked HyperTech shield in a running Uma
 * process and dump its full rwxp region to /sdcard/shield.bin.
 * Argv: <pid>
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define SIG_STR    "checkLoadPath_extractNativeLibs_true"
#define SIG_LEN    (sizeof(SIG_STR) - 1)
#define SIG_OFFSET 0x12205
#define MIN_SIZE   0x13000
#define MAX_SIZE   0x80000
#define OUT_PATH   "/sdcard/shield.bin"

static void *memmem_local(const void *h, size_t hlen,
                          const void *n, size_t nlen) {
    if (nlen == 0 || hlen < nlen) return NULL;
    const uint8_t *hp = h;
    const uint8_t first = ((const uint8_t *)n)[0];
    for (size_t i = 0; i + nlen <= hlen; i++)
        if (hp[i] == first && memcmp(hp + i, n, nlen) == 0)
            return (void *)(hp + i);
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s <pid>\n", argv[0]); return 2; }
    pid_t pid = atoi(argv[1]);
    if (pid <= 0) return 2;

    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/maps", pid);
    FILE *mf = fopen(path, "r");
    if (!mf) { perror("open maps"); return 2; }
    snprintf(path, sizeof(path), "/proc/%d/mem", pid);
    int memfd = open(path, O_RDONLY);
    if (memfd < 0) { perror("open mem"); fclose(mf); return 2; }

    uint8_t *buf = malloc(MAX_SIZE);
    char line[512];
    uint64_t best_start = 0, best_end = 0;
    while (fgets(line, sizeof(line), mf)) {
        uint64_t start, end; char perms[8];
        int off_hex, dmaj, dmin, inode; char rest[256] = {0};
        int n = sscanf(line, "%lx-%lx %7s %x %x:%x %d %255[^\n]",
                       &start, &end, perms, &off_hex, &dmaj, &dmin, &inode, rest);
        if (n < 7) continue;
        if (perms[2] != 'x') continue;
        int has_file = 0;
        for (int i = 0; rest[i]; i++)
            if (rest[i] != ' ' && rest[i] != '\t') { has_file = 1; break; }
        if (has_file) continue;
        uint64_t size = end - start;
        if (size < MIN_SIZE || size > MAX_SIZE) continue;
        ssize_t r = pread64(memfd, buf, size, (off64_t)start);
        if (r <= 0) continue;
        uint8_t *hit = memmem_local(buf, (size_t)r, SIG_STR, SIG_LEN);
        if (hit) {
            best_start = start;
            best_end = end;
            uint64_t sig_addr = start + (uint64_t)(hit - buf);
            uint64_t shield_base = sig_addr - SIG_OFFSET;
            printf("FOUND region=%lx-%lx sig=0x%lx shield_base=0x%lx\n",
                   (unsigned long)start, (unsigned long)end,
                   (unsigned long)sig_addr, (unsigned long)shield_base);
            // Dump
            FILE *out = fopen(OUT_PATH, "wb");
            if (!out) { perror("fopen out"); free(buf); close(memfd); fclose(mf); return 2; }
            fwrite(buf, 1, (size_t)r, out);
            fclose(out);
            printf("WROTE %s size=%zd\n", OUT_PATH, r);
            break;
        }
    }
    free(buf);
    close(memfd);
    fclose(mf);
    if (best_end == 0) { printf("NOT_FOUND\n"); return 1; }
    return 0;
}
