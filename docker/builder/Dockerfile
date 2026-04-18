FROM arm32v7/debian:bullseye

SHELL ["/bin/bash", "-i", "-c"]

COPY requirements.txt /requirements.txt

# Install required packages
RUN apt-get update

RUN apt-get install -y zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev \
    libssl-dev libreadline-dev libffi-dev libsqlite3-dev \
    wget libbz2-dev curl gnupg ca-certificates apt-transport-https nano

RUN apt-get install -y build-essential cmake git unzip pkg-config
RUN apt-get install -y libjpeg-dev libtiff-dev libpng-dev
RUN apt-get install -y libavcodec-dev libavformat-dev libswscale-dev
RUN apt-get install -y libgtk2.0-dev libcanberra-gtk* libgtk-3-dev
RUN apt-get install -y libgstreamer1.0-dev gstreamer1.0-gtk3
RUN apt-get install -y libgstreamer-plugins-base1.0-dev gstreamer1.0-gl
RUN apt-get install -y libxvidcore-dev libx264-dev

RUN apt-get install -y python3-dev python3-numpy python3-pip python3-setuptools python3-tk
RUN apt-get install -y libtbb2 libtbb-dev libdc1394-22-dev
RUN apt-get install -y libv4l-dev v4l-utils
RUN apt-get install -y libopenblas-dev libatlas-base-dev libblas-dev
RUN apt-get install -y liblapack-dev gfortran libhdf5-dev
RUN apt-get install -y libprotobuf-dev libgoogle-glog-dev libgflags-dev
RUN apt-get install -y protobuf-compiler

# Install OpenCV
RUN wget -O opencv.zip https://github.com/opencv/opencv/archive/4.5.5.zip
RUN wget -O opencv_contrib.zip https://github.com/opencv/opencv_contrib/archive/4.5.5.zip
RUN unzip opencv.zip
RUN unzip opencv_contrib.zip
RUN mv opencv-4.5.5 opencv
RUN mv opencv_contrib-4.5.5 opencv_contrib
RUN rm opencv.zip
RUN rm opencv_contrib.zip
RUN mkdir -p /opencv/build
WORKDIR /opencv/build
RUN cmake -D CMAKE_BUILD_TYPE=RELEASE \
    -D CMAKE_INSTALL_PREFIX=/usr/local \
    -D OPENCV_EXTRA_MODULES_PATH=/opencv_contrib/modules \
    -D ENABLE_NEON=ON \
    -D WITH_OPENMP=ON \
    -D WITH_OPENCL=OFF \
    -D BUILD_TIFF=ON \
    -D WITH_FFMPEG=ON \
    -D WITH_TBB=ON \
    -D BUILD_TBB=ON \
    -D WITH_GSTREAMER=ON \
    -D BUILD_TESTS=OFF \
    -D WITH_EIGEN=OFF \
    -D WITH_V4L=ON \
    -D WITH_LIBV4L=ON \
    -D WITH_VTK=OFF \
    -D WITH_QT=OFF \
    -D OPENCV_ENABLE_NONFREE=ON \
    -D INSTALL_C_EXAMPLES=OFF \
    -D INSTALL_PYTHON_EXAMPLES=OFF \
    -D OPENCV_FORCE_LIBATOMIC_COMPILER_CHECK=1 \
    -D PYTHON3_PACKAGES_PATH=/usr/lib/python3/dist-packages \
    -D OPENCV_GENERATE_PKGCONFIG=ON \
    -D BUILD_EXAMPLES=OFF ..
RUN make -j4
RUN make install
RUN ldconfig
RUN make clean

WORKDIR /

RUN apt-get update

# Install Python packages
RUN pip3 install --upgrade pip setuptools
RUN pip3 install pyinstaller
RUN pip3 --timeout=14400 install -r /requirements.txt

# Install Google Cloud CLI
RUN curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
RUN echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list
RUN apt-get update
RUN apt-get install -y google-cloud-cli
