/*
 * shield_watchdog — locate + patch the shield, then keep pread64'ing the
 * writer/reader bytes to detect if the shield restores them. If it does,
 * log and re-patch. Runs until the process exits.
 *
 * Argv: <pid> [poll_ms=50] [seconds=60]
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
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
    for (size_t i = 0; i + nlen <= hlen; i++)
        if (hp[i] == first && memcmp(hp + i, n, nlen) == 0)
            return (void *)(hp + i);
    return NULL;
}

static int find_shield(pid_t pid, uint64_t *base_out) {
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/maps", pid);
    FILE *mf = fopen(path, "r");
    if (!mf) return -1;
    snprintf(path, sizeof(path), "/proc/%d/mem", pid);
    int memfd = open(path, O_RDONLY);
    if (memfd < 0) { fclose(mf); return -1; }

    uint8_t *buf = malloc(MAX_SIZE);
    char line[512];
    int found = 0;
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
            *base_out = sig_addr - SIG_OFFSET;
            found = 1;
            break;
        }
    }
    free(buf);
    fclose(mf);
    close(memfd);
    return found ? 0 : -1;
}

static long ms_now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: %s <pid> [poll_ms] [seconds]\n", argv[0]); return 2; }
    pid_t pid = atoi(argv[1]);
    int poll_ms = argc > 2 ? atoi(argv[2]) : 50;
    int total_sec = argc > 3 ? atoi(argv[3]) : 60;
    if (pid <= 0) return 2;

    uint64_t base = 0;
    if (find_shield(pid, &base) != 0) { printf("NOT_FOUND\n"); return 1; }
    printf("FOUND shield_base=0x%lx\n", (unsigned long)base);

    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/mem", pid);
    int memfd = open(path, O_RDWR);
    if (memfd < 0) { perror("open mem"); return 2; }

    uint64_t writer = base + WRITER_OFFSET;
    uint64_t reader = base + READER_OFFSET;

    // Snapshot original bytes before patching (what shield has in them now).
    uint8_t orig_w[4], orig_r[8];
    if (pread64(memfd, orig_w, 4, (off64_t)writer) != 4) {
        perror("pread orig writer"); return 2;
    }
    if (pread64(memfd, orig_r, 8, (off64_t)reader) != 8) {
        perror("pread orig reader"); return 2;
    }
    printf("ORIG writer=");
    for (int i = 0; i < 4; i++) printf("%02x", orig_w[i]);
    printf(" reader=");
    for (int i = 0; i < 8; i++) printf("%02x", orig_r[i]);
    printf("\n");

    // Apply patches.
    pwrite64(memfd, WRITER_PATCH, 4, (off64_t)writer);
    pwrite64(memfd, READER_PATCH, 8, (off64_t)reader);
    printf("PATCHED at t+0ms\n");
    fflush(stdout);

    long start = ms_now();
    long last_verify = 0;
    long repatches = 0, polls = 0;
    uint8_t buf_w[4], buf_r[8];
    while (1) {
        long now = ms_now();
        if ((now - start) / 1000 >= total_sec) break;

        ssize_t rw = pread64(memfd, buf_w, 4, (off64_t)writer);
        ssize_t rr = pread64(memfd, buf_r, 8, (off64_t)reader);
        if (rw != 4 || rr != 8) {
            printf("MEMFD_GONE t+%ldms polls=%ld repatches=%ld\n",
                   now - start, polls, repatches);
            break;
        }
        polls++;
        int w_match = memcmp(buf_w, WRITER_PATCH, 4) == 0;
        int r_match = memcmp(buf_r, READER_PATCH, 8) == 0;
        if (!w_match || !r_match) {
            printf("DRIFT t+%ldms w_match=%d r_match=%d writer=",
                   now - start, w_match, r_match);
            for (int i = 0; i < 4; i++) printf("%02x", buf_w[i]);
            printf(" reader=");
            for (int i = 0; i < 8; i++) printf("%02x", buf_r[i]);
            printf("\n");
            fflush(stdout);
            pwrite64(memfd, WRITER_PATCH, 4, (off64_t)writer);
            pwrite64(memfd, READER_PATCH, 8, (off64_t)reader);
            repatches++;
        }
        if (now - last_verify > 500) {
            printf("POLL t+%ldms polls=%ld repatches=%ld w_match=%d r_match=%d\n",
                   now - start, polls, repatches, w_match, r_match);
            fflush(stdout);
            last_verify = now;
        }
        struct timespec ts = { poll_ms / 1000, (poll_ms % 1000) * 1000000L };
        nanosleep(&ts, NULL);
    }
    close(memfd);
    return 0;
}
