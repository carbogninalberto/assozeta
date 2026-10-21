/* Integration-only process failures. Never included as a runtime executable. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (getenv("ASSOZETA_TEST_SECRET") || argc != 6 || strcmp(argv[1], "encode") || strcmp(argv[2], "L")) return 3;
    switch (atoi(argv[3])) {
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
    case 6: { unsigned char bytes[] = {255,216,255,217}; return fwrite(bytes, 1, sizeof(bytes), stdout) == sizeof(bytes) ? 0 : 3; }
    default: return 2;
    }
}
