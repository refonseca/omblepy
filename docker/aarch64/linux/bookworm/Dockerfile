FROM arm64v8/debian:bookworm

SHELL ["/bin/bash", "-i", "-c"]

COPY requirements.txt /requirements.txt
COPY entrypoint.sh /entrypoint.sh

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    python3-setuptools \
    python3-wheel \
    build-essential \
    binutils

# --break-system-packages is required on Debian Bookworm (PEP 668 / Python 3.11).
RUN pip install --break-system-packages --upgrade pip setuptools wheel \
    && pip install --break-system-packages -r /requirements.txt \
    && pip install --break-system-packages pyinstaller

WORKDIR /src/
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
