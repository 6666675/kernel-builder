FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG REPO_REV=97dc5c1bd9527c2abe2183b16a4b7ef037dc34a7
ARG MKBOOTIMG_REV=d2bb0af5ba6d3198a3e99529c97eda1be0b5a093

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash bc bison build-essential ca-certificates cpio curl dwarves flex git gnupg \
    file kmod libelf-dev liblz4-tool libncurses-dev libssl-dev libtinfo5 lz4 openssl pkg-config \
    python3 python3-pip rsync unzip util-linux xz-utils zip zstd \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system user.name 'CNB Kernel Builder' \
    && git config --system user.email 'kernel-builder@localhost' \
    && git config --system gc.auto 0

RUN git init /opt/git-repo \
    && git -C /opt/git-repo remote add origin https://gerrit.googlesource.com/git-repo \
    && git -C /opt/git-repo fetch --depth=1 origin "$REPO_REV" \
    && git -C /opt/git-repo checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/git-repo rev-parse HEAD)" = "$REPO_REV" \
    && cp /opt/git-repo/repo /usr/local/bin/repo \
    && chmod 755 /usr/local/bin/repo \
    && git init /opt/mkbootimg \
    && git -C /opt/mkbootimg remote add origin https://android.googlesource.com/platform/system/tools/mkbootimg \
    && git -C /opt/mkbootimg fetch --depth=1 origin "$MKBOOTIMG_REV" \
    && git -C /opt/mkbootimg checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/mkbootimg rev-parse HEAD)" = "$MKBOOTIMG_REV"

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 PYTHONUNBUFFERED=1 \
    REPO_URL=https://gerrit.googlesource.com/git-repo \
    REPO_REV=97dc5c1bd9527c2abe2183b16a4b7ef037dc34a7 \
    MKBOOTIMG_DIR=/opt/mkbootimg
WORKDIR /workspace
CMD ["bash"]
