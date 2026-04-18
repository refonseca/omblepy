#!/bin/bash
# Checks required files and tools
if [ ! -f "omblepy.py" ] || [ ! -d "docker" ] || [ ! -f "script_core.sh" ]; then
    echo "Please execute this script within the omblepy root folder."
    exit 1
fi

# Import auxiliary functions
source script_core.sh

if ! command_exists docker; then
    echo "Docker is not installed in the current system, this script cannot execute."
    echo "Please install docker version 19.03 or higher and try again."
    exit 1
fi

# On MSYS/Git Bash (Windows) the shell auto-converts Unix paths to Windows paths
# before passing them to subprocesses.  That breaks Docker volume mounts and
# in-container path arguments (e.g. /src/ becomes C:/Program Files/Git/src/).
# Fix: disable path conversion with MSYS_NO_PATHCONV=1, and use the native
# Windows path for the volume source (pwd -W) so Docker Desktop can resolve it.
if [[ "$(uname -o 2>/dev/null)" == "Msys" ]]; then
    DOCKER_SRC=$(pwd -W)          # e.g. D:/Projetos/Veus/omblepy
    DOCKER_ENV="MSYS_NO_PATHCONV=1"
else
    DOCKER_SRC=$(pwd)
    DOCKER_ENV=""
fi

reset
echo "omblepy release builder"
echo
echo "Select an option by inputting a number:"
echo "1 - Compile binary"
echo "2 - Compile docker image"
echo "3 - Load docker image"
echo "4 - Save docker image"
echo "5 - Execute docker image"
echo "6 - Configure QEMU and Buildx (necessary for ARM compilation when host is not ARM)"

read OPTION
echo

# Assertive
if [ -z $OPTION ] || (($OPTION < 1 || $OPTION > 6)); then
    echo "Invalid option."
    echo "Script execution aborted."
    exit 1
fi

# ------------------------------------ Compile binary ------------------------------------
if (($OPTION == 1)); then
    # Defines IMAGE_LIST variable
    select_platform

    # For each image in image list
    for IMAGE in "${IMAGE_LIST[@]}"; do
        if [ ! -z $IMAGE ]; then
            echo
            echo "Building omblepy for: $IMAGE"

            # MSYS_NO_PATHCONV=1 prevents Git Bash from converting /src/... paths.
            # ${DOCKER_SRC} is the Windows-native project root path for Docker Desktop.
            env ${DOCKER_ENV} docker run -t \
                -v "${DOCKER_SRC}:/src" \
                ${IMAGE}:quick \
                "pyinstaller /src/omblepy.spec --clean --distpath /src/dist"

            echo
            echo "Copying binary to release/${IMAGE}/"
            mkdir -p release/${IMAGE}
            cp -f dist/omblepy release/${IMAGE}/omblepy
        fi
    done

# ---------------------------------- Compile docker image ----------------------------------
elif (($OPTION == 2)); then
    # Get host architecture
    ARCH_HOST=$(uname -m)
    echo "Host architecture: $ARCH_HOST"

    # Defines IMAGE_LIST variable
    select_platform

    for IMAGE in "${IMAGE_LIST[@]}"; do
        if [ ! -z $IMAGE ]; then
            echo
            echo "Copying requirements.txt for Image: $IMAGE"
            cp -f requirements.txt docker/${IMAGE}/requirements.txt
            echo
            echo "Compiling Image: $IMAGE"

            IMAGE_ARCH=$(echo "$IMAGE" | cut -d'/' -f1)
            echo "Image architecture: $IMAGE_ARCH"

            case "$IMAGE_ARCH" in
            armv7)
                PLATFORM="linux/arm/v7"
                ;;
            armv6)
                PLATFORM="linux/arm/v6"
                ;;
            aarch64)
                PLATFORM="linux/arm64"
                ;;
            *)
                echo "Unknown architecture for image: $IMAGE"
                continue
                ;;
            esac

            echo "Target platform: $PLATFORM"

            if [[ "$ARCH_HOST" == *"$IMAGE_ARCH"* ]]; then
                docker build -t "$IMAGE:quick" "docker/${IMAGE}"
            else
                docker buildx build --platform "$PLATFORM" -t "$IMAGE:quick" "docker/${IMAGE}" --load
            fi
        fi
    done

# ----------------------------------- Load docker image ------------------------------------
elif (($OPTION == 3)); then
    select_platform

    for IMAGE in "${IMAGE_LIST[@]}"; do
        if [ ! -z $IMAGE ]; then
            echo
            echo "Loading Image: $IMAGE"
            docker login
            REPOSITORY=$(echo "$IMAGE" | tr / _)
            docker pull veus1/${REPOSITORY}
            docker tag veus1/${REPOSITORY} ${IMAGE}:quick
        fi
    done

# ----------------------------------- Save docker image ------------------------------------
elif (($OPTION == 4)); then
    select_platform

    for IMAGE in "${IMAGE_LIST[@]}"; do
        if [ ! -z $IMAGE ]; then
            echo
            echo "Saving Image: $IMAGE"
            docker login
            REPOSITORY=$(echo "$IMAGE" | tr / _)
            docker tag ${IMAGE}:quick veus1/${REPOSITORY}
            docker push veus1/${REPOSITORY}
        fi
    done

# --------------------------------- Execute docker image ----------------------------------
elif (($OPTION == 5)); then
    select_platform

    for IMAGE in "${IMAGE_LIST[@]}"; do
        if [ ! -z $IMAGE ]; then
            echo
            echo "Executing Image: $IMAGE"
            env ${DOCKER_ENV} docker run -i -t \
                -v "${DOCKER_SRC}:/src" \
                ${IMAGE}:quick /bin/bash
        fi
    done

# --------------------------------- Configure QEMU and Docker Buildx -----------------------
elif (($OPTION == 6)); then
    docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
    docker buildx create --name omblepy-builder --use || docker buildx use omblepy-builder
    docker buildx inspect --bootstrap
    echo "Buildx and QEMU have been configured successfully."
fi

echo
echo "Script execution ended."
