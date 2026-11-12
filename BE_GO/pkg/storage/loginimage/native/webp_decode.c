/* Process-isolated Pillow-compatible WebP first-frame admission/decoding.
 * webp-info|webp-decode INPUT_BYTES MAX_WORKING_BYTES; exact bytes on stdin.
 * AZWP output: width/height, channels and three zero bytes, frame count,
 * loop count, background ARGB and four zero bytes (all integers big endian),
 * then packed RGB/RGBA pixels only for webp-decode. Exit 2 malformed input,
 * 3 I/O/codec failure, 4 logical budget/allocation failure. No malformed-input
 * result is emitted. The Go parent owns timeout, cancellation and output bounds.
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <webp/decode.h>
#include <webp/demux.h>

static int positive_size(const char *s, size_t *result) {
    size_t n = 0;
    if (!*s) return 0;
    for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
        if (*p < '0' || *p > '9' || n > (SIZE_MAX - (*p - '0')) / 10) return 0;
        n = n * 10 + (*p - '0');
    }
    *result = n;
    return n != 0;
}
static void put32(unsigned char *p, uint32_t n) {
    p[0] = (unsigned char)(n >> 24); p[1] = (unsigned char)(n >> 16);
    p[2] = (unsigned char)(n >> 8); p[3] = (unsigned char)n;
}

int process_webp(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "webp-version")) {
        int v = WebPGetDecoderVersion();
        return printf("%d.%d.%d\n", (v >> 16) & 255, (v >> 8) & 255, v & 255) < 0 ? 3 : 0;
    }
    size_t input_size, limit;
    int pixels = argc > 1 && !strcmp(argv[1], "webp-decode");
    if (argc != 4 || (!pixels && strcmp(argv[1], "webp-info")) ||
        !positive_size(argv[2], &input_size) || !positive_size(argv[3], &limit)) return 2;
    if (input_size > limit) return 4;
    unsigned char *input = malloc(input_size), *row = NULL;
    if (!input) return 4;
    int status = 2, channels = 4;
    WebPDemuxer *probe = NULL;
    WebPAnimDecoder *decoder = NULL;
    if (fread(input, 1, input_size, stdin) != input_size || fgetc(stdin) != EOF || ferror(stdin)) goto done;
    if (input_size < 16 || memcmp(input, "RIFF", 4) || memcmp(input + 8, "WEBP", 4) ||
        (memcmp(input + 12, "VP8 ", 4) && memcmp(input + 12, "VP8L", 4) && memcmp(input + 12, "VP8X", 4))) goto done;
    WebPData data = {input, input_size};
    probe = WebPDemux(&data);
    if (!probe) goto done;
    uint32_t width = WebPDemuxGetI(probe, WEBP_FF_CANVAS_WIDTH);
    uint32_t height = WebPDemuxGetI(probe, WEBP_FF_CANVAS_HEIGHT);
    if (!width || !height || (uint64_t)width * height > 178956970) goto done;
    /* AnimDecoder allocates two RGBA canvases, including for lazy admission.
     * Bound these before constructing it. Demux/codec internal allocations and
     * allocator overhead are not a total-process RSS guarantee. */
    size_t count = (size_t)width * height;
    WebPBitstreamFeatures features;
    if (WebPGetFeatures(input, input_size, &features) == VP8_STATUS_OK && !features.has_alpha) channels = 3;
    size_t used = input_size;
    if (count > (limit - used) / 8) { status = 4; goto done; }
    used += count * 8;
    if (pixels && channels == 3) {
        if ((size_t)width > (limit - used) / 3) { status = 4; goto done; }
        row = malloc((size_t)width * 3);
        if (!row) { status = 4; goto done; }
    }
    WebPDemuxDelete(probe); probe = NULL;
    decoder = WebPAnimDecoderNew(&data, NULL);
    if (!decoder) goto done;
    WebPAnimInfo info;
    if (!WebPAnimDecoderGetInfo(decoder, &info) || info.canvas_width != width || info.canvas_height != height || !info.frame_count) goto done;
    uint8_t *rgba = NULL;
    int timestamp;
    if (pixels && !WebPAnimDecoderGetNext(decoder, &rgba, &timestamp)) goto done;
    unsigned char header[32] = {'A','Z','W','P'};
    put32(header + 4, width); put32(header + 8, height); header[12] = (unsigned char)channels;
    put32(header + 16, info.frame_count); put32(header + 20, info.loop_count); put32(header + 24, info.bgcolor);
    status = 3;
    if (fwrite(header, 1, sizeof(header), stdout) != sizeof(header)) goto done;
    if (pixels && channels == 4) {
        if (fwrite(rgba, 4, count, stdout) != count) goto done;
    } else if (pixels) {
        for (uint32_t y = 0; y < height; ++y) {
            const uint8_t *src = rgba + (size_t)y * width * 4;
            for (uint32_t x = 0; x < width; ++x) memcpy(row + (size_t)x * 3, src + (size_t)x * 4, 3);
            if (fwrite(row, 3, width, stdout) != width) goto done;
        }
    }
    if (fflush(stdout)) goto done;
    status = 0;
done:
    WebPAnimDecoderDelete(decoder); WebPDemuxDelete(probe);
    free(row); free(input);
    return status;
}
