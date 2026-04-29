/*
 * shield_svc_patch — find shield base, patch the `svc #0` at
 * shield_base + 0x12130 to `nop`. Used to test whether the shield's
 * generic syscall wrapper is on Uma's kill path.
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
#define SVC_OFFSET 0x12130
#define MIN_SIZE   0x13000
#define MAX_SIZE   0x80000

static const uint8_t NOP_PATCH[4] = { 0x1f, 0x20, 0x03, 0xd5 };
static const uint8_t SVC_ORIG[4]  = { 0x01, 0x00, 0x00, 0xd4 };

static void *memmem_local(const void *h, size_t hlen,
                          const void *n, size_t nlen) {
    if (nlen == 0 || hlen < nlen) return NULL;
    const uint8_t *hp = h;
    for (size_t i = 0; i + nlen <= hlen; i++)
        if (hp[i] == ((const uint8_t *)n)[0] && memcmp(hp + i, n, nlen) == 0)
            return (void *)(hp + i);
    return NULL;
}

int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s <pid>\n", argv[0]); return 2; }
    pid_t pid = atoi(argv[1]);

    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/maps", pid);
    FILE *mf = fopen(path, "r");
    snprintf(path, sizeof(path), "/proc/%d/mem", pid);
    int memfd = open(path, O_RDWR);
    if (!mf || memfd < 0) { perror("open"); return 2; }

    uint8_t *buf = malloc(MAX_SIZE);
    char line[512];
    uint64_t shield_base = 0;

    while (fgets(line, sizeof(line), mf)) {
        uint64_t start, end; char perms[8];
        int off_hex, dmaj, dmin, inode; char rest[256] = {0};
        int n = sscanf(line, "%lx-%lx %7s %x %x:%x %d %255[^\n]",
                       &start, &end, perms, &off_hex, &dmaj, &dmin, &inode, rest);
        if (n < 7 || perms[2] != 'x') continue;
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
            printf("shield_base=0x%lx\n", (unsigned long)shield_base);
            break;
        }
    }
    free(buf); fclose(mf);
    if (shield_base == 0) { printf("NOT_FOUND\n"); close(memfd); return 1; }

    uint64_t svc_addr = shield_base + SVC_OFFSET;

    // Verify original
    uint8_t cur[4];
    if (pread64(memfd, cur, 4, (off64_t)svc_addr) != 4) { perror("pread svc"); close(memfd); return 2; }
    printf("svc_addr=0x%lx current=%02x%02x%02x%02x expect=%02x%02x%02x%02x\n",
           (unsigned long)svc_addr, cur[0], cur[1], cur[2], cur[3],
           SVC_ORIG[0], SVC_ORIG[1], SVC_ORIG[2], SVC_ORIG[3]);
    if (memcmp(cur, SVC_ORIG, 4) != 0) {
        fprintf(stderr, "UNEXPECTED bytes at svc site — aborting\n");
        close(memfd); return 3;
    }

    // Patch
    if (pwrite64(memfd, NOP_PATCH, 4, (off64_t)svc_addr) != 4) {
        perror("pwrite"); close(memfd); return 2;
    }
    if (pread64(memfd, cur, 4, (off64_t)svc_addr) != 4) { perror("verify"); close(memfd); return 2; }
    printf("after_patch=%02x%02x%02x%02x %s\n", cur[0], cur[1], cur[2], cur[3],
           memcmp(cur, NOP_PATCH, 4) == 0 ? "OK" : "FAIL");
    close(memfd);
    return 0;
}
