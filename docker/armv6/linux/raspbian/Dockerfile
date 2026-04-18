FROM arm32v7/debian:jessie

SHELL ["/bin/bash", "-i", "-c"]

# Copy files
COPY qemu-arm-static /usr/bin
COPY requirements.txt /requirements.txt
COPY entrypoint.sh /entrypoint.sh

ENV READTHEDOCS=True

# Update environment
RUN apt-get update

# Install Python
RUN apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-setuptools \
    python3-dev \
    python3-wheel

# Install other required packages
RUN apt-get install -y --no-install-recommends build-essential \
    libssl-dev \
    libffi-dev \
    zlib1g-dev

# Set aliases for packages
RUN echo "alias python=/usr/bin/python3" >> ~/.bashrc \
    && echo "alias pip=/usr/bin/pip3" >> ~/.bashrc \
    && source ~/.bashrc

# Install Python packages
RUN pip install --upgrade pip==19.3.1 setuptools==41.6.0 \
    && pip install -r /requirements.txt \
    && pip install pyinstaller==3.4

# Define working directory and entrypoint
WORKDIR /src/
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

