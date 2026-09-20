# syntax=docker/dockerfile:1.7
FROM docker:27-cli
RUN apk add --no-cache python3 curl openssl util-linux git
WORKDIR /runner
COPY selfhost/updater/*.py ./
COPY BE/instance/release_catalog.py ./
ARG VERSION=development
ENV RUNNER_VERSION=$VERSION
ENTRYPOINT ["python3"]
CMD ["/runner/server.py"]
