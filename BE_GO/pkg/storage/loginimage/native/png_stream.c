/* PNG compressed-data reader. Mirrors Pillow 11.1 load_read and ZipDecode's
 * row-limited inflate calls; metadata and pixel unpacking remain in Go.
 * png-inflate INPUT_BYTES WIDTH HEIGHT BITS INTERLACE OFFSET LENGTH SEQUENCE MAX_RAW
 * stdin: exact file bytes. stdout: AZPN frame only after complete decoding.
 * Exit 2: invalid input; 3: codec/allocation/I/O failure; 4: working budget.
 * See README.md and LICENSE.pillow for algorithm provenance. */
#include <stdint.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

static int number(const char *s, size_t *out) {
    if (!*s) return 0;
    size_t n = 0;
    for (; *s; ++s) {
        if (*s < '0' || *s > '9' || n > (SIZE_MAX - (*s - '0')) / 10) return 0;
        n = n * 10 + (*s - '0');
    }
    *out = n;
    return 1;
}
static uint32_t be32(const unsigned char *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static void put64(unsigned char *p, uint64_t n) {
    for (int i = 7; i >= 0; --i) { p[i] = (unsigned char)n; n >>= 8; }
}
static int chunk_name(const unsigned char *p) {
    for (int i = 0; i < 4; ++i) {
        unsigned char c = p[i];
        if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') || c == '_')) return 0;
    }
    return 1;
}
struct pass { size_t width, height, row; };

int inflate_png(int argc, char **argv) {
    size_t length, width, height, bits, interlace, position, remaining, limit, seq;
    if (argc != 11 || !number(argv[2], &length) || !length ||
        !number(argv[3], &width) || !width || !number(argv[4], &height) || !height ||
        !number(argv[5], &bits) || !number(argv[6], &interlace) || interlace > 1 ||
        !number(argv[7], &position) || position > length ||
        !number(argv[8], &remaining) || remaining > UINT32_MAX ||
        !number(argv[10], &limit) || !limit) return 2;
    uint64_t sequence = UINT64_MAX;
    if (strcmp(argv[9], "-1")) {
        if (!number(argv[9], &seq) || seq > UINT32_MAX) return 2;
        sequence = seq;
    }
    if (!(bits == 1 || bits == 2 || bits == 4 || bits == 8 || bits == 16 ||
          bits == 24 || bits == 32 || bits == 48 || bits == 64)) return 2;
    static const size_t x0[7] = {0,4,0,2,0,1,0}, y0[7] = {0,0,4,0,2,0,1};
    static const size_t dx[7] = {8,8,4,4,2,2,1}, dy[7] = {8,8,8,4,4,2,2};
    struct pass passes[7];
    size_t count = 0, total = 0;
    for (size_t i = 0; i < (interlace ? 7U : 1U); ++i) {
        size_t w = width, h = height;
        if (interlace) {
            if (width <= x0[i] || height <= y0[i]) continue;
            w = 1 + (width - x0[i] - 1) / dx[i];
            h = 1 + (height - y0[i] - 1) / dy[i];
        }
        if (w > (SIZE_MAX - 7) / bits) return 4;
        size_t row = (w * bits + 7) / 8 + 1;
        if (row > UINT_MAX || h > (limit - total) / row) return 4;
        total += row * h;
        passes[count++] = (struct pass){w,h,row};
    }
    unsigned char *input = malloc(length), *raw = malloc(total);
    if (!input || !raw) { free(input); free(raw); return 3; }
    int status = 2, initialized = 0;
    z_stream z = {0};
    if (fread(input, 1, length, stdin) != length || fgetc(stdin) != EOF || ferror(stdin)) goto done;
    if (inflateInit(&z) != Z_OK) { status = 3; goto done; }
    initialized = 1;
    size_t pass = 0, row_index = 0, used = 0, partial = 0;
    for (;;) {
        while (remaining == 0) {
            /* load_read skips CRCs and admits IDAT/DDAT/fdAT only. */
            position += length - position < 4 ? length - position : 4;
            if (length - position < 8) goto done;
            const unsigned char *header = input + position;
            if (!chunk_name(header + 4)) goto done;
            remaining = be32(header);
            position += 8;
            if (!memcmp(header + 4, "fdAT", 4)) {
                if (remaining < 4 || length - position < 4) goto done;
                uint64_t next = be32(input + position);
                if (sequence == UINT64_MAX || next != sequence + 1) goto done;
                sequence = next;
                position += 4;
                remaining -= 4;
            } else if (memcmp(header + 4, "IDAT", 4) && memcmp(header + 4, "DDAT", 4)) goto done;
        }
        size_t request = remaining < 65536 ? remaining : 65536;
        remaining -= request; /* Pillow subtracts requested, not actual bytes. */
        size_t available = length - position < request ? length - position : request;
        if (!available) goto done;
        z.next_in = input + position;
        z.avail_in = (uInt)available;
        position += available;
        while (z.avail_in > 0) {
            size_t row = passes[pass].row;
            z.next_out = raw + used + partial;
            z.avail_out = (uInt)(row - partial);
            int result = inflate(&z, Z_NO_FLUSH);
            if (result < 0) { status = result == Z_MEM_ERROR ? 3 : 2; goto done; }
            partial = row - z.avail_out;
            if (partial < row) break;
            if (raw[used] > 4) goto done;
            used += row;
            partial = 0;
            if (++row_index == passes[pass].height) { ++pass; row_index = 0; }
            if (pass == count || result == Z_STREAM_END) {
                unsigned char frame[40] = {'A','Z','P','N'};
                put64(frame + 8, position);
                put64(frame + 16, remaining);
                put64(frame + 24, sequence);
                put64(frame + 32, used);
                status = fwrite(frame,1,sizeof(frame),stdout) == sizeof(frame) &&
                         fwrite(raw,1,used,stdout) == used && fflush(stdout) == 0 ? 0 : 3;
                goto done;
            }
        }
    }
done:
    if (initialized) inflateEnd(&z);
    free(input);
    free(raw);
    return status;
}
