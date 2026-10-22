FROM golang:1.25.12-alpine AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /server ./cmd/server

# Keep the codec independent of Go's CGO toolchain and isolate codec faults in a
# bounded child process. Static linking also avoids cross-stage musl ABI drift.
FROM alpine:3.21 AS codec-builder
RUN apk add --no-cache build-base libjpeg-turbo-dev libjpeg-turbo-static libjpeg-turbo-doc zlib-dev zlib-static libwebp-dev libwebp-static libwebp-doc pkgconf
COPY pkg/storage/loginimage/native/*.c /src/
RUN cc -std=c11 -O2 -Wall -Wextra -Werror -static /src/jpeg_encode.c /src/jpeg_decode.c /src/png_stream.c /src/webp_decode.c -ljpeg -lz $(pkg-config --static --libs libwebpdemux libwebp) -lm -o /image-codec && \
    apk info -v | grep '^libjpeg-turbo-static-' > /codec-version && \
    apk info -v | grep '^zlib-static-' > /zlib-version && \
    apk info -v | grep '^libwebp-static-' > /webp-version && \
    sed '/^#ifndef ZLIB_H/,$d' /usr/include/zlib.h > /zlib-license

FROM alpine:3.21
ARG VERSION=v0.0.0
ARG CODE_REVISION=development
RUN apk add --no-cache ca-certificates tzdata && \
    addgroup -S -g 10001 assozeta && adduser -S -D -H -u 10001 -G assozeta assozeta
WORKDIR /app
RUN printf '%s\n' "$VERSION" > /app/VERSION && \
    printf '%s\n' "$CODE_REVISION" > /app/CODE_REVISION
COPY --from=builder /server /app/server
COPY --from=codec-builder /image-codec /app/image-codec
COPY --from=codec-builder /codec-version /app/licenses/libjpeg-turbo-version.txt
COPY --from=codec-builder /zlib-version /app/licenses/zlib-version.txt
COPY --from=codec-builder /zlib-license /app/licenses/zlib-LICENSE
COPY --from=codec-builder /webp-version /app/licenses/libwebp-version.txt
COPY --from=codec-builder /usr/share/doc/libwebp/PATENTS /app/licenses/libwebp-PATENTS
COPY pkg/storage/loginimage/LICENSE.libwebp /app/licenses/libwebp-LICENSE
COPY --from=codec-builder /usr/share/doc/libjpeg-turbo/LICENSE.md /app/licenses/libjpeg-turbo-LICENSE.md
COPY --from=codec-builder /usr/share/doc/libjpeg-turbo/README.ijg /app/licenses/libjpeg-turbo-README.ijg
COPY pkg/storage/loginimage/NOTICE /app/licenses/loginimage-NOTICE
COPY migrations /app/migrations
COPY pkg/storage/loginimage/LICENSE.pillow /app/licenses/Pillow-LICENSE
COPY pkg/qrcode/LICENSE.go-qrcode /app/licenses/go-qrcode-LICENSE
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8000/readyz || exit 1
ENTRYPOINT ["/app/server"]
