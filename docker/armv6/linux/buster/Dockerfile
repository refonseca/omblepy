FROM arm32v7/debian:buster

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
    python3.7 \
    python3-pip \
    python3-setuptools \
    python3-dev \
    python3-wheel

# Install other required packages
RUN apt-get install -y --no-install-recommends build-essential \
    libgl1-mesa-glx \
    libssl-dev \
    libffi-dev \
    zlib1g-dev \
    libatlas-base-dev \
    libhdf5-dev \
    libhdf5-serial-dev \
    libatlas-base-dev \
    libjasper-dev  \
    libqtgui4  \ 
    libqt4-test

RUN apt-get -y install cmake

# Set aliases for packages
RUN echo "alias python=/usr/bin/python3.7" >> ~/.bashrc \
    && echo "alias pip=/usr/bin/pip3" >> ~/.bashrc \
    && source ~/.bashrc

# Install Python packages
RUN pip install --upgrade pip setuptools==57.0.0 wheel==0.36.2 \
    && pip3 install rpi.gpio \
    && pip3 install picamera \
    && pip3 install -r /requirements.txt \
    && pip3 install pyinstaller==4.2

# Define working directory and entrypoint
WORKDIR /src/
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

