# syntax=docker/dockerfile:1.7
FROM docker:29-cli
RUN apk add --no-cache python3 curl openssl util-linux git
WORKDIR /runner
COPY selfhost/updater/*.py ./
COPY selfhost/bin/check-manual-image ./check-manual-image
COPY BE/instance/release_catalog.py ./
ARG VERSION=development
ENV RUNNER_VERSION=$VERSION
ENTRYPOINT ["python3"]
CMD ["/runner/server.py"]
