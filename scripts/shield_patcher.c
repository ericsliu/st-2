/*
 * shield_patcher — find HyperTech CrackProof unpacked shield in a running
 * com.cygames.umamusume process and neutralize the writer + reader via
 * /proc/<pid>/mem pwrite64. Root required.
 *
 * Build (aarch64):
 *   $NDK/.../aarch64-linux-android29-clang -O2 -static-libstdc++ \
 *       scripts/shield_patcher.c -o shield_patcher
 *
 * Usage:
 *   shield_patcher <pid>
 * Exits: 0 = patched, 1 = not found, 2 = error
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#define SIG_STR        "checkLoadPath_extractNativeLibs_true"
#define SIG_LEN        (sizeof(SIG_STR) - 1)
#define SIG_OFFSET     0x12205
#define WRITER_OFFSET  0x11910
#define READER_OFFSET  0x118ec

#define MIN_SIZE       0x13000
#define MAX_SIZE       0x80000

static const uint8_t WRITER_PATCH[4] = { 0xc0, 0x03, 0x5f, 0xd6 };
static const uint8_t READER_PATCH[8] = {
    0x00, 0x00, 0x80, 0x52, 0xc0, 0x03, 0x5f, 0xd6
};

static void *memmem_local(const void *h, size_t hlen,
                          const void *n, size_t nlen) {
    if (nlen == 0 || hlen < nlen) return NULL;
    const uint8_t *hp = h;
    const uint8_t first = ((const uint8_t *)n)[0];
    for (size_t i = 0; i + nlen <= hlen; i++) {
        if (hp[i] == first && memcmp(hp + i, n, nlen) == 0)
            return (void *)(hp + i);
    }
    return NULL;
}

static int patch_region(int memfd, uint64_t addr,
                        const uint8_t *data, size_t len) {
    ssize_t w = pwrite64(memfd, data, len, (off64_t)addr);
    if (w < 0) { fprintf(stderr, "pwrite64 0x%lx: %s\n",
                         (unsigned long)addr, strerror(errno)); return -1; }
    if ((size_t)w != len) { fprintf(stderr, "short write 0x%lx: %zd/%zu\n",
                                    (unsigned long)addr, w, len); return -1; }
    return 0;
}

static int verify_region(int memfd, uint64_t addr,
                         const uint8_t *expect, size_t len) {
    uint8_t buf[16];
    if (len > sizeof(buf)) return -1;
    ssize_t r = pread64(memfd, buf, len, (off64_t)addr);
    if (r < 0) return -1;
    if ((size_t)r != len) return -1;
    return memcmp(buf, expect, len) == 0 ? 0 : 1;
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s <pid>\n", argv[0]); return 2; }
    pid_t pid = atoi(argv[1]);
    if (pid <= 0) { fprintf(stderr, "bad pid\n"); return 2; }

    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/maps", pid);
    FILE *mapsf = fopen(path, "r");
    if (!mapsf) { fprintf(stderr, "open %s: %s\n", path, strerror(errno)); return 2; }

    snprintf(path, sizeof(path), "/proc/%d/mem", pid);
    int memfd = open(path, O_RDWR);
    if (memfd < 0) { fprintf(stderr, "open %s: %s\n", path, strerror(errno));
                     fclose(mapsf); return 2; }

    char line[512];
    int candidates = 0, scanned = 0, found = 0;
    uint64_t shield_base = 0;

    uint8_t *buf = malloc(MAX_SIZE);
    if (!buf) { perror("malloc"); close(memfd); fclose(mapsf); return 2; }

    while (fgets(line, sizeof(line), mapsf)) {
        uint64_t start, end;
        char perms[8];
        int off_hex, dev_maj, dev_min, inode;
        char rest[256] = {0};
        int n = sscanf(line, "%lx-%lx %7s %x %x:%x %d %255[^\n]",
                       &start, &end, perms, &off_hex, &dev_maj, &dev_min,
                       &inode, rest);
        if (n < 7) continue;
        if (perms[2] != 'x') continue;
        // Must be anon (no backing file). rest may be empty or contain
        // whitespace only. Skip lines with any non-whitespace text.
        int has_file = 0;
        for (int i = 0; rest[i]; i++)
            if (rest[i] != ' ' && rest[i] != '\t') { has_file = 1; break; }
        if (has_file) continue;

        uint64_t size = end - start;
        if (size < MIN_SIZE || size > MAX_SIZE) continue;

        candidates++;
        ssize_t r = pread64(memfd, buf, size, (off64_t)start);
        if (r <= 0) continue;
        scanned++;

        uint8_t *hit = memmem_local(buf, (size_t)r, SIG_STR, SIG_LEN);
        if (hit) {
            uint64_t sig_addr = start + (uint64_t)(hit - buf);
            shield_base = sig_addr - SIG_OFFSET;
            printf("FOUND region=%lx-%lx sig_addr=0x%lx shield_base=0x%lx perms=%s\n",
                   (unsigned long)start, (unsigned long)end,
                   (unsigned long)sig_addr, (unsigned long)shield_base, perms);
            found = 1;
            break;
        }
    }
    free(buf);
    fclose(mapsf);

    if (!found) {
        printf("NOT_FOUND candidates=%d scanned=%d\n", candidates, scanned);
        close(memfd);
        return 1;
    }

    uint64_t writer_addr = shield_base + WRITER_OFFSET;
    uint64_t reader_addr = shield_base + READER_OFFSET;

    if (patch_region(memfd, writer_addr, WRITER_PATCH, sizeof(WRITER_PATCH)) != 0 ||
        patch_region(memfd, reader_addr, READER_PATCH, sizeof(READER_PATCH)) != 0) {
        close(memfd);
        return 2;
    }

    int vw = verify_region(memfd, writer_addr, WRITER_PATCH, sizeof(WRITER_PATCH));
    int vr = verify_region(memfd, reader_addr, READER_PATCH, sizeof(READER_PATCH));
    printf("PATCHED writer=0x%lx reader=0x%lx verify_writer=%d verify_reader=%d\n",
           (unsigned long)writer_addr, (unsigned long)reader_addr, vw, vr);
    close(memfd);
    return (vw == 0 && vr == 0) ? 0 : 2;
}
