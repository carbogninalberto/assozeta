/* Narrow, process-isolated JPEG encoder for the Go login image adapter.
 * Uses libjpeg's default quality (75), sampling, DCT and marker policy, as
 * Pillow 11.1.0 does for Image.save(format="JPEG") without encoder options.
 * Protocol: encode MODE WIDTH HEIGHT MAX_OUTPUT_BYTES [COMMENT_BYTES];
 * optional comment followed by packed raster on stdin.
 * No bytes are emitted until complete input and successful encoding are proven.
 * Exit 2: invalid input; 3: codec/allocation/I/O failure; 4: output budget.
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <setjmp.h>
#include <jpeglib.h>

struct encoder {
    struct jpeg_compress_struct jpeg;
    struct jpeg_error_mgr error;
    struct jpeg_destination_mgr dest;
    jmp_buf failed;
    int status;
    unsigned char chunk[4096];
    unsigned char *row, *output, *comment;
    size_t used, capacity, limit;
};

static void fail(struct encoder *s, int status) {
    s->status = status;
    longjmp(s->failed, 1);
}
static void codec_error(j_common_ptr jpeg) {
    fail((struct encoder *)jpeg->client_data, 3);
}
static void silent_message(j_common_ptr jpeg) { (void)jpeg; }
static void append(struct encoder *s, size_t size) {
    if (size > s->limit - s->used) fail(s, 4);
    size_t needed = s->used + size;
    if (needed > s->capacity) {
        size_t capacity = s->capacity ? s->capacity : sizeof(s->chunk);
        if (capacity > s->limit) capacity = s->limit;
        while (capacity < needed) {
            if (capacity > s->limit / 2) { capacity = s->limit; break; }
            capacity *= 2;
        }
        void *memory = realloc(s->output, capacity);
        if (!memory) fail(s, 3);
        s->output = memory;
        s->capacity = capacity;
    }
    if (size) memcpy(s->output + s->used, s->chunk, size);
    s->used = needed;
}
static void destination_init(j_compress_ptr jpeg) {
    struct encoder *s = jpeg->client_data;
    s->dest.next_output_byte = s->chunk;
    s->dest.free_in_buffer = sizeof(s->chunk);
}
static boolean destination_full(j_compress_ptr jpeg) {
    struct encoder *s = jpeg->client_data;
    append(s, sizeof(s->chunk));
    destination_init(jpeg);
    return TRUE;
}
static void destination_finish(j_compress_ptr jpeg) {
    struct encoder *s = jpeg->client_data;
    append(s, sizeof(s->chunk) - s->dest.free_in_buffer);
}
static int positive_size(const char *value, size_t *result) {
    if (!*value) return 0;
    size_t number = 0;
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        if (*p < '0' || *p > '9' || number > (SIZE_MAX - (*p - '0')) / 10) return 0;
        number = number * 10 + (*p - '0');
    }
    *result = number;
    return number != 0;
}
int decode_jpeg(int argc, char **argv);

int main(int argc, char **argv) {
    if (argc > 1 && !strcmp(argv[1], "decode")) return decode_jpeg(argc, argv);
    size_t width, height, limit, comment_size = 0;
    if ((argc != 6 && argc != 7) || strcmp(argv[1], "encode") ||
        !positive_size(argv[3], &width) || !positive_size(argv[4], &height) ||
        !positive_size(argv[5], &limit) || width > JPEG_MAX_DIMENSION || height > JPEG_MAX_DIMENSION) return 2;
    if (argc == 7 && ((strcmp(argv[6], "0") && !positive_size(argv[6], &comment_size)) || comment_size > 65533)) return 2;
    int channels;
    J_COLOR_SPACE color;
    if (!strcmp(argv[2], "L")) { channels = 1; color = JCS_GRAYSCALE; }
    else if (!strcmp(argv[2], "RGB")) { channels = 3; color = JCS_RGB; }
    else if (!strcmp(argv[2], "RGBX")) { channels = 4; color = JCS_EXT_RGBX; }
    else if (!strcmp(argv[2], "YCbCr")) { channels = 3; color = JCS_YCbCr; }
    else if (!strcmp(argv[2], "CMYK")) { channels = 4; color = JCS_CMYK; }
    else return 2;
    size_t pillow_block = width * 4 > 65536 ? width * 4 : 65536;
    size_t marker_prefix = color == JCS_CMYK ? 18 : 20;
    if (argc == 7 && comment_size + marker_prefix + 4 >= pillow_block) return 2;
    if (width > SIZE_MAX / (size_t)channels / height) return 2;
    struct encoder *s = calloc(1, sizeof(*s));
    if (!s) return 3;
    s->limit = limit;
    s->jpeg.err = jpeg_std_error(&s->error);
    s->error.error_exit = codec_error;
    s->error.output_message = silent_message;
    s->jpeg.client_data = s;
    /* All state modified after setjmp is heap-owned; cleanup remains defined
     * after a libjpeg longjmp, including partially constructed codec state. */
    if (setjmp(s->failed)) {
        int status = s->status;
        jpeg_destroy_compress(&s->jpeg);
        free(s->row); free(s->output); free(s->comment); free(s);
        return status;
    }
    jpeg_create_compress(&s->jpeg);
    s->jpeg.image_width = (JDIMENSION)width;
    s->jpeg.image_height = (JDIMENSION)height;
    s->jpeg.input_components = channels;
    s->jpeg.in_color_space = color;
    jpeg_set_defaults(&s->jpeg);
    s->dest.init_destination = destination_init;
    s->dest.empty_output_buffer = destination_full;
    s->dest.term_destination = destination_finish;
    s->jpeg.dest = &s->dest;
    size_t row_size = width * (size_t)channels;
    s->row = malloc(row_size);
    if (!s->row) fail(s, 3);
    if (argc == 7) {
        s->comment = malloc(comment_size ? comment_size : 1);
        if (!s->comment) fail(s, 3);
        if (fread(s->comment, 1, comment_size, stdin) != comment_size) fail(s, 2);
    }
    jpeg_start_compress(&s->jpeg, TRUE);
    if (s->comment) jpeg_write_marker(&s->jpeg, JPEG_COM, s->comment, (unsigned int)comment_size);
    while (s->jpeg.next_scanline < s->jpeg.image_height) {
        if (fread(s->row, 1, row_size, stdin) != row_size) fail(s, 2);
        /* Pillow's CMYK;I packing uses inverted Adobe CMYK samples. */
        if (color == JCS_CMYK)
            for (size_t i = 0; i < row_size; ++i) s->row[i] = (unsigned char)(255 - s->row[i]);
        JSAMPROW row = s->row;
        if (jpeg_write_scanlines(&s->jpeg, &row, 1) != 1) fail(s, 3);
    }
    if (fgetc(stdin) != EOF || ferror(stdin)) fail(s, 2);
    jpeg_finish_compress(&s->jpeg);
    if (fwrite(s->output, 1, s->used, stdout) != s->used || fflush(stdout)) fail(s, 3);
    jpeg_destroy_compress(&s->jpeg);
    free(s->row); free(s->output); free(s->comment); free(s);
    return 0;
}
