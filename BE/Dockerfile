# pull official base image
FROM python:3.13.2-bullseye

# set work directory
WORKDIR /usr/src/app

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies (optimized - removed unused: tesseract-ocr, poppler-utils, ffmpeg, libev-dev, sqlite3, vim)
RUN apt-get update && apt-get -y upgrade && \
    apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl wget gnupg2 \
    libsm6 libxext6 && \
    rm -rf /var/lib/apt/lists/*

# Install PostgreSQL 15 client (matches server version)
RUN echo "deb http://apt.postgresql.org/pub/repos/apt bullseye-pgdg main" > /etc/apt/sources.list.d/pgdg.list && \
    wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | apt-key add - && \
    apt-get update && \
    apt-get install -y postgresql-client-15 && \
    rm -rf /var/lib/apt/lists/*

RUN apt-get update && \
    apt-get install -y locales && \
    sed -i -e 's/# it_IT.UTF-8 UTF-8/it_IT.UTF-8 UTF-8/' /etc/locale.gen && \
    dpkg-reconfigure --frontend=noninteractive locales

ENV LANG it_IT.UTF-8
ENV LC_ALL it_IT.UTF-8

# install dependencies
RUN pip3.13 install --upgrade pip
RUN pip3.13 install --upgrade setuptools wheel
COPY ./requirements.txt .
RUN pip3.13 install -r requirements.txt && rm -rf /root/.cache

# copy project
COPY . .

# Version injection from build arg
ARG VERSION=v0.0.0
RUN echo "${VERSION}" > /usr/src/app/VERSION

# Run entrypoint
CMD ["/usr/src/app/entrypoint.sh"]