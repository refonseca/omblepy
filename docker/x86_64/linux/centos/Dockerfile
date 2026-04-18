FROM centos:7
SHELL ["/bin/bash", "-i", "-c"]

ARG PYTHON_VERSION=3.6.1
ARG PYINSTALLER_VERSION=3.5

ENV PYPI_URL=https://pypi.python.org/
ENV PYPI_INDEX_URL=https://pypi.python.org/simple
ENV PYENV_VERSION=${PYTHON_VERSION}

COPY entrypoint.sh /entrypoint.sh

COPY requirements.txt /requirements.txt

RUN \
    set -x \
    && yum clean all \
    && yum makecache \
    && yum update -y \
    && yum install -y \
        yum-utils \
        groupinstall "Development Tools" \
        ca-certificates \
        curl \
        git \
        gcc \
        make \
        kernel-devel \
        libffi-devel \
        zlib \
        sqlite \
        readline \
        bzip2 \
        bzip2-libs \
        openssl-devel \
        python36u-libs \
        python36u-pip \
        python36u-devel \
    && echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc \
    && echo 'export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc \
    && source ~/.bashrc \
    && curl -L https://github.com/pyenv/pyenv-installer/raw/master/bin/pyenv-installer | bash \
    && echo 'eval "$(pyenv init -)"' >> ~/.bashrc \
    && source ~/.bashrc \
    && PYTHON_CONFIGURE_OPTS="--enable-shared" pyenv install $PYTHON_VERSION \
    && pyenv global $PYTHON_VERSION \
    && pip install --upgrade pip \
    && pip install pyinstaller==$PYINSTALLER_VERSION \
    && mkdir /src/ \
    && chmod +x /entrypoint.sh

RUN \
    set -x \
    && pip install -r /requirements.txt

WORKDIR /src/

ENTRYPOINT ["/entrypoint.sh"]
