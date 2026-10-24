/* Process-isolated 8-bit JPEG pixel decoding, matching Pillow's ordinary
 * (non-draft) libjpeg settings. This is not Pillow's lazy metadata parser.
 * Protocol: decode INPUT_BYTES MAX_RASTER_BYTES, JPEG file on stdin.
 * Success: AZJP, big-endian width/height, channel byte, 3 zero bytes,
 * big-endian COM length (0xffffffff absent), comment, pixels.
 * Exit 2: malformed/unsupported JPEG or input; 3: allocation/I/O; 4: budget.
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <setjmp.h>
#include <jpeglib.h>

struct decoder {
    struct jpeg_decompress_struct jpeg;
    struct jpeg_error_mgr error;
    struct jpeg_source_mgr source;
    jmp_buf failed;
    int status;
    unsigned char *input, *pixels, *comment;
    size_t comment_size;
};
static void decode_fail(struct decoder *s, int status) {
    s->status = status;
    longjmp(s->failed, 1);
}
static void decode_error(j_common_ptr jpeg) { decode_fail(jpeg->client_data, 2); }
static void quiet(j_common_ptr jpeg) { (void)jpeg; }
static void source_noop(j_decompress_ptr jpeg) { (void)jpeg; }
/* Do not synthesize EOI at EOF (jpeg_mem_src's default). Pillow suspends. */
static boolean source_empty(j_decompress_ptr jpeg) { (void)jpeg; return FALSE; }
static void source_skip(j_decompress_ptr jpeg, long count) {
    if (count <= 0) return;
    size_t amount = (size_t)count;
    if (amount > jpeg->src->bytes_in_buffer) amount = jpeg->src->bytes_in_buffer;
    jpeg->src->next_input_byte += amount;
    jpeg->src->bytes_in_buffer -= amount;
}
static int parse_size(const char *text, size_t *out) {
    if (!*text) return 0;
    size_t n = 0;
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        if (*p < '0' || *p > '9' || n > (SIZE_MAX - (*p - '0')) / 10) return 0;
        n = n * 10 + (*p - '0');
    }
    *out = n;
    return n != 0;
}
static void put32(unsigned char *out, JDIMENSION value) {
    out[0] = (unsigned char)(value >> 24); out[1] = (unsigned char)(value >> 16);
    out[2] = (unsigned char)(value >> 8); out[3] = (unsigned char)value;
}
static int decode_stream(size_t length, size_t limit) {
    struct decoder *s = calloc(1, sizeof(*s));
    if (!s) return 3;
    s->jpeg.err = jpeg_std_error(&s->error);
    s->error.error_exit = decode_error;
    s->error.output_message = quiet;
    s->jpeg.client_data = s;
    if (setjmp(s->failed)) {
        int status = s->status;
        jpeg_destroy_decompress(&s->jpeg);
        free(s->input); free(s->pixels); free(s->comment); free(s);
        return status;
    }
    s->input = malloc(length);
    if (!s->input) decode_fail(s, 3);
    if (fread(s->input, 1, length, stdin) != length || fgetc(stdin) != EOF || ferror(stdin)) decode_fail(s, 2);
    if (length < 3 || s->input[0] != 255 || s->input[1] != 216 || s->input[2] != 255) decode_fail(s, 2);
    jpeg_create_decompress(&s->jpeg);
    s->source.init_source = source_noop;
    s->source.fill_input_buffer = source_empty;
    s->source.skip_input_data = source_skip;
    s->source.resync_to_restart = jpeg_resync_to_restart;
    s->source.term_source = source_noop;
    s->source.next_input_byte = s->input;
    s->source.bytes_in_buffer = length;
    s->jpeg.src = &s->source;
    jpeg_save_markers(&s->jpeg, JPEG_COM, 65535);
    int status;
    do { status = jpeg_read_header(&s->jpeg, FALSE); } while (status == JPEG_HEADER_TABLES_ONLY);
    if (status != JPEG_HEADER_OK || s->jpeg.data_precision != 8) decode_fail(s, 2);
    jpeg_saved_marker_ptr last = NULL;
    for (jpeg_saved_marker_ptr marker = s->jpeg.marker_list; marker; marker = marker->next)
        if (marker->marker == JPEG_COM) last = marker;
    if (last) {
        s->comment_size = last->data_length;
        s->comment = malloc(s->comment_size ? s->comment_size : 1);
        if (!s->comment) decode_fail(s, 3);
        if (s->comment_size) memcpy(s->comment, last->data, s->comment_size);
    }
    int channels = s->jpeg.num_components;
    if (channels == 1) s->jpeg.out_color_space = JCS_GRAYSCALE;
    else if (channels == 3) s->jpeg.out_color_space = JCS_RGB;
    else if (channels == 4) s->jpeg.out_color_space = JCS_CMYK;
    else decode_fail(s, 2);
    size_t width = s->jpeg.image_width, height = s->jpeg.image_height;
    if (!width || !height || width > JPEG_MAX_DIMENSION || height > JPEG_MAX_DIMENSION || width > SIZE_MAX / (size_t)channels / height) decode_fail(s, 2);
    size_t size = width * height * (size_t)channels;
    if (size > limit) decode_fail(s, 4);
    s->pixels = malloc(size);
    if (!s->pixels) decode_fail(s, 3);
    if (!jpeg_start_decompress(&s->jpeg)) decode_fail(s, 2);
    if (s->jpeg.output_width != width || s->jpeg.output_height != height || s->jpeg.output_components != channels) decode_fail(s, 2);
    while (s->jpeg.output_scanline < s->jpeg.output_height) {
        JSAMPROW row = s->pixels + (size_t)s->jpeg.output_scanline * width * (size_t)channels;
        if (jpeg_read_scanlines(&s->jpeg, &row, 1) != 1) decode_fail(s, 2);
        /* Pillow always uses CMYK;I, including files without an Adobe marker. */
        if (channels == 4)
            for (size_t i = 0; i < width * (size_t)channels; ++i) row[i] = (unsigned char)(255 - row[i]);
    }
    /* Pillow tolerates finish suspension once every scanline was produced;
     * fatal libjpeg errors still fail, and earlier suspension is always invalid. */
    (void)jpeg_finish_decompress(&s->jpeg);
    unsigned char header[20] = {'A','Z','J','P',0,0,0,0,0,0,0,0,0,0,0,0,255,255,255,255};
    put32(header + 4, s->jpeg.output_width); put32(header + 8, s->jpeg.output_height);
    header[12] = (unsigned char)channels;
    if (s->comment) put32(header + 16, (JDIMENSION)s->comment_size);
    if (fwrite(header, 1, sizeof(header), stdout) != sizeof(header) || (s->comment_size && fwrite(s->comment, 1, s->comment_size, stdout) != s->comment_size) || fwrite(s->pixels, 1, size, stdout) != size || fflush(stdout)) decode_fail(s, 3);
    jpeg_destroy_decompress(&s->jpeg);
    free(s->input); free(s->pixels); free(s->comment); free(s);
    return 0;
}

/* Parse outside the setjmp frame: input arguments are immutable while decoding,
 * and GCC need not treat inlined parser locals as longjmp-clobbered state. */
int decode_jpeg(int argc, char **argv) {
    size_t length, limit;
    if (argc != 4 || !parse_size(argv[2], &length) || !parse_size(argv[3], &limit)) return 2;
    return decode_stream(length, limit);
}
