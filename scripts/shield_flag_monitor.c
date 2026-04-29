/*
 * shield_flag_monitor — locate shield_base, then poll the detection flag
 * buffer at shield_base + 0x2f0f0 every POLL_MS ms. Log timestamped deltas
 * to stderr so we can correlate with Hachimi "Hooking finished" and exit.
 *
 * Also scans the 256 bytes starting at the struct base so we see context.
 *
 * Argv: <pid> [poll_ms] [total_sec]
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define SIG_STR    "checkLoadPath_extractNativeLibs_true"
#define SIG_LEN    (sizeof(SIG_STR) - 1)
#define SIG_OFFSET 0x12205
#define MIN_SIZE   0x13000
#define MAX_SIZE   0x80000
#define STRUCT_OFF 0x2f0f0
#define WINDOW     256

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

static double now_s(struct timespec *t0) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    double d = (t.tv_sec - t0->tv_sec) + (t.tv_nsec - t0->tv_nsec) / 1e9;
    return d;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <pid> [poll_ms] [total_sec]\n", argv[0]); return 2; }
    pid_t pid = atoi(argv[1]);
    int poll_ms = (argc > 2) ? atoi(argv[2]) : 20;
    int total_sec = (argc > 3) ? atoi(argv[3]) : 30;
    if (pid <= 0) return 2;

    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/maps", pid);
    FILE *mf = fopen(path, "r");
    if (!mf) { perror("open maps"); return 2; }
    snprintf(path, sizeof(path), "/proc/%d/mem", pid);
    int memfd = open(path, O_RDONLY);
    if (memfd < 0) { perror("open mem"); fclose(mf); return 2; }

    // Find shield_base
    uint8_t *buf = malloc(MAX_SIZE);
    char line[512];
    uint64_t shield_base = 0;
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
            break;
        }
    }
    free(buf);
    fclose(mf);
    if (shield_base == 0) { fprintf(stderr, "NOT_FOUND\n"); close(memfd); return 1; }
    uint64_t flag_addr = shield_base + STRUCT_OFF;
    fprintf(stderr, "shield_base=0x%lx flag_addr=0x%lx poll=%dms total=%ds\n",
            (unsigned long)shield_base, (unsigned long)flag_addr,
            poll_ms, total_sec);

    // Poll loop
    uint8_t prev[WINDOW]; memset(prev, 0, sizeof(prev));
    uint8_t cur[WINDOW];
    int first = 1;
    struct timespec t0; clock_gettime(CLOCK_MONOTONIC, &t0);
    long total_us = (long)total_sec * 1000 * 1000;
    long elapsed_us = 0;
    while (elapsed_us < total_us) {
        ssize_t r = pread64(memfd, cur, WINDOW, (off64_t)flag_addr);
        if (r < 0) {
            fprintf(stderr, "[%.3f] pread FAIL errno=%d (%s)\n",
                    now_s(&t0), errno, strerror(errno));
            break;
        }
        if (first || memcmp(prev, cur, WINDOW) != 0) {
            fprintf(stderr, "[%.3f] flag+hdr:", now_s(&t0));
            // Print first 32 bytes + all non-zero bytes with their offset
            fprintf(stderr, " first32=");
            for (int i = 0; i < 32; i++) fprintf(stderr, "%02x", cur[i]);
            // Then print any non-zero offsets in [32, WINDOW)
            int any = 0;
            for (int i = 32; i < WINDOW; i++) {
                if (cur[i] != 0) { any = 1; break; }
            }
            if (any) {
                fprintf(stderr, " nonzero=[");
                for (int i = 0; i < WINDOW; i++)
                    if (cur[i]) fprintf(stderr, "+%x:%02x ", i, cur[i]);
                fprintf(stderr, "]");
            }
            fprintf(stderr, "\n");
            memcpy(prev, cur, WINDOW);
            first = 0;
        }
        usleep((useconds_t)poll_ms * 1000);
        elapsed_us += poll_ms * 1000;
    }
    fprintf(stderr, "[%.3f] DONE\n", now_s(&t0));
    close(memfd);
    return 0;
}
