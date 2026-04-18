FROM arm32v7/debian:bullseye

SHELL ["/bin/bash", "-i", "-c"]

COPY qemu-arm-static /usr/bin
COPY requirements.txt /requirements.txt
COPY entrypoint.sh /entrypoint.sh

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    python3-setuptools \
    python3-wheel \
    build-essential \
    binutils \
    upx-ucl

RUN pip3 install --upgrade pip setuptools wheel \
    && pip3 install -r /requirements.txt \
    && pip3 install pyinstaller

WORKDIR /src/
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
