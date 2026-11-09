# syntax=docker/dockerfile:1.7
FROM docker:29-cli
# GNU tar lets backups delete staged files while archiving them; tzdata gives
# scheduled backups the installation’s local time; age and boto3 encrypt and
# upload the remote copies.
RUN apk add --no-cache python3 py3-boto3 age curl openssl util-linux git tar tzdata
WORKDIR /runner
COPY selfhost/updater/*.py ./
COPY selfhost/bin/check-manual-image ./check-manual-image
COPY BE/instance/release_catalog.py ./
ARG VERSION=development
ENV RUNNER_VERSION=$VERSION
ENTRYPOINT ["python3"]
CMD ["/runner/server.py"]
