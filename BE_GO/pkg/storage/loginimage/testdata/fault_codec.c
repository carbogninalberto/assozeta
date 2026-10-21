/* Integration-only process failures. Never included as a runtime executable. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (getenv("ASSOZETA_TEST_SECRET")) return 3;
    int decoding = argc == 4 && !strcmp(argv[1], "decode");
    int png = argc == 11 && !strcmp(argv[1], "png-inflate");
    int code;
    if (png) code = atoi(argv[3]);
    else if (decoding) code = atoi(argv[2]) - 3;
    else if (argc == 6 && !strcmp(argv[1], "encode") &&
             (!strcmp(argv[2], "L") || !strcmp(argv[2], "RGB"))) code = atoi(argv[3]);
    else return 3;
    switch (code) {
    case 1: {
        char path[4096];
        if (snprintf(path, sizeof(path), "%s.started", argv[0]) >= (int)sizeof(path)) return 3;
        FILE *started = fopen(path, "a");
        if (!started) return 3;
        fputc('1', started); fclose(started);
        for (;;) pause();
    }
    case 2:
        for (;;) { unsigned char bytes[4096] = {0}; if (fwrite(bytes, 1, sizeof(bytes), stdout) != sizeof(bytes)) return 3; }
    case 3: return 4;
    case 4: return 3;
    case 5: fputs("invalid JPEG", stdout); return 0;
    case 6: {
        if (png) {
            unsigned char frame[47] = {'A','Z','P','N'};
            frame[15] = 3;
            memset(frame + 24,255,8);
            frame[39] = 7;
            memset(frame + 41,42,6);
            return fwrite(frame,1,sizeof(frame),stdout) == sizeof(frame) ? 0 : 3;
        }
        if (decoding) {
            unsigned char frame[] = {'A','Z','J','P',0,0,0,1,0,0,0,1,1,0,0,0,255,255,255,255,42};
            return fwrite(frame, 1, sizeof(frame), stdout) == sizeof(frame) ? 0 : 3;
        }
        unsigned char bytes[] = {255,216,255,217}; return fwrite(bytes, 1, sizeof(bytes), stdout) == sizeof(bytes) ? 0 : 3; }
    default: return 2;
    }
}
