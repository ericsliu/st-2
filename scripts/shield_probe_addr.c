/*
 * shield_probe_addr — locate shield base via signature, then probe the
 * detection struct region (shield_base + 0x2f0f0) decoded from reader.
 *
 * Outputs:
 *   1) shield_base
 *   2) Every /proc/<pid>/maps entry whose range overlaps
 *      [shield_base, shield_base + 0x50000]
 *   3) pread64 probes at shield_base + {0x17000, 0x2f000, 0x2f0f0, 0x2f140}
 *      showing either the bytes read or the errno.
 *
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

static void probe(int memfd, uint64_t addr, const char *label) {
    uint8_t buf[32] = {0};
    errno = 0;
    ssize_t r = pread64(memfd, buf, sizeof(buf), (off64_t)addr);
    if (r < 0) {
        printf("probe %s (0x%lx): FAIL errno=%d (%s)\n",
               label, (unsigned long)addr, errno, strerror(errno));
        return;
    }
    printf("probe %s (0x%lx): OK %zd bytes: ", label,
           (unsigned long)addr, r);
    for (ssize_t i = 0; i < r; i++) printf("%02x ", buf[i]);
    printf("\n");
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
    uint64_t shield_base = 0;

    // Pass 1: find shield_base
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
            uint64_t sig_addr = start + (uint64_t)(hit - buf);
            shield_base = sig_addr - SIG_OFFSET;
            printf("shield_base=0x%lx code_region=%lx-%lx\n",
                   (unsigned long)shield_base,
                   (unsigned long)start, (unsigned long)end);
            break;
        }
    }
    free(buf);

    if (shield_base == 0) {
        printf("NOT_FOUND\n");
        close(memfd); fclose(mf);
        return 1;
    }

    uint64_t window_start = shield_base;
    uint64_t window_end   = shield_base + 0x50000;
    printf("scan_window=0x%lx-0x%lx\n",
           (unsigned long)window_start, (unsigned long)window_end);

    // Pass 2: print every maps entry whose range overlaps the window
    rewind(mf);
    while (fgets(line, sizeof(line), mf)) {
        uint64_t start, end;
        int n = sscanf(line, "%lx-%lx", &start, &end);
        if (n != 2) continue;
        if (end <= window_start) continue;
        if (start >= window_end) continue;
        // Trim trailing newline
        size_t L = strlen(line);
        while (L && (line[L-1] == '\n' || line[L-1] == '\r')) { line[--L] = 0; }
        printf("overlap: %s\n", line);
    }

    // Pass 3: pread64 at key offsets decoded from reader/writer
    probe(memfd, shield_base + 0x17000, "end_of_code");
    probe(memfd, shield_base + 0x2f000, "adrp_page");
    probe(memfd, shield_base + 0x2f0f0, "struct_base");
    probe(memfd, shield_base + 0x2f140, "struct+0x50");

    close(memfd);
    fclose(mf);
    return 0;
}
