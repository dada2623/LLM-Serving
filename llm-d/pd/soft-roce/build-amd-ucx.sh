#!/usr/bin/env bash
# Run in the AMD vLLM 0.33 builder with the ROCm runtime mounts.
set -euo pipefail
if [[ $# != 2 ]]; then
  echo "Usage: $0 <configured UCX 1.23.0 source directory> <extracted rdma-core /usr>" >&2
  exit 2
fi
unset LD_PRELOAD UCX_MODULE_DIR
source_dir=$(cd "$1" && pwd)
rdma_usr=$(cd "$2" && pwd)
rdma_lib="$rdma_usr/lib/x86_64-linux-gnu"
rocm_sdk=/srv/llm-serving/venvs/vllm-rocm100-v033/lib/python3.12/site-packages/_rocm_sdk_devel
prefix=/srv/llm-serving/pd-runtime/ucx-rxe-rocm100
cd "$source_dir"
./configure --version | sed -n '1p' | grep -F '1.23.0'
test -d "$rocm_sdk"
test -f "$rdma_usr/include/infiniband/verbs.h"
test -e "$rdma_lib/librdmacm.so.1"
ln -sf /usr/lib/x86_64-linux-gnu/libibverbs.so.1 "$rdma_lib/libibverbs.so.1"
CPPFLAGS="-I$rdma_usr/include" LDFLAGS="-L$rdma_lib" ./configure \
  --prefix="$prefix" --with-rocm="$rocm_sdk" --with-verbs --with-rdmacm="$rdma_usr" \
  --without-cuda --without-java --without-go --disable-numa --disable-doxygen-doc \
  --disable-debug --enable-mt
make -j"${UCX_BUILD_JOBS:-6}"
make install
cp -L "$rdma_lib/librdmacm.so.1" "$prefix/lib/librdmacm.so.1"
test -e "$prefix/lib/ucx/libuct_ib.so.0"
test -e "$prefix/lib/ucx/libuct_rocm.so.0"
LD_LIBRARY_PATH="$prefix/lib:${LD_LIBRARY_PATH:-}" "$prefix/bin/ucx_info" -v
