FROM golang:1.25.12-alpine AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /server ./cmd/server

FROM alpine:3.21
RUN apk add --no-cache ca-certificates tzdata && \
    addgroup -S -g 10001 assozeta && adduser -S -D -H -u 10001 -G assozeta assozeta
WORKDIR /app
COPY --from=builder /server /app/server
COPY migrations /app/migrations
COPY pkg/storage/loginimage/LICENSE.pillow /app/licenses/Pillow-LICENSE
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8000/readyz || exit 1
ENTRYPOINT ["/app/server"]
