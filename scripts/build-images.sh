#!/usr/bin/env bash
# 构建本项目的三个自研镜像：mediary-scout-web / mediary-strm-sync / mediary-qqbot
#
# Mediary Scout 上游不发布预构建镜像，本脚本会：
#   1. 克隆（或更新）上游源码到 vendor/mediary-scout
#   2. 构建 web 镜像（跨平台时用 --platform）
#   3. 构建 qqbot / strm-sync 镜像
#   4. 可选：--save 导出所有镜像为一个 tar（用于往 NAS 上搬运）
#
# 用法：
#   ./scripts/build-images.sh                    # 为本机架构构建
#   ./scripts/build-images.sh --platform linux/amd64   # 为 x86 NAS 构建（Apple Silicon Mac 上打包必用）
#   ./scripts/build-images.sh --platform linux/amd64 --save dist/mediastack-images.tar
set -euo pipefail
cd "$(dirname "$0")/.."

PLATFORM=""
SAVE_PATH=""
UPSTREAM_REPO="https://github.com/fancydirty/mediary-scout.git"
UPSTREAM_DIR="vendor/mediary-scout"
# 墙内构建加速：npm 源与 Docker Hub 镜像（按需修改或留空）
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
DOCKER_MIRROR="${DOCKER_MIRROR:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform) PLATFORM="$ 2"; shift 2 ;;
    --save) SAVE_PATH="$ 2"; shift 2 ;;
  # shellcheck disable=SC2016
    *) echo "未知参数: $1" >& 2; exit 1 ;;
  esac
done

echo "==> 拉取 Mediary Scout 上游源码"
if [[ -d "$UPSTREAM_DIR/.git" ]]; then
  git -C "$UPSTREAM_DIR" fetch --tags origin
  git -C "$UPSTREAM_DIR" pull --ff-only
else
  git clone "$UPSTREAM_REPO" "$UPSTREAM_DIR"
fi
GIT_SHA=$(git -C "$UPSTREAM_DIR" rev-parse HEAD)
echo "    上游 commit: $GIT_SHA"

BUILD_ARGS=(--build-arg "GIT_SHA=$GIT_SHA" --build-arg "NPM_REGISTRY=$NPM_REGISTRY")
[[ -n "$DOCKER_MIRROR" ]] && BUILD_ARGS+=(--build-arg "DOCKER_MIRROR=$DOCKER_MIRROR")
PLATFORM_ARGS=()
[[ -n "$PLATFORM" ]] && PLATFORM_ARGS=(--platform "$PLATFORM")

echo "==> 构建 mediary-scout-web"
docker buildx build "${PLATFORM_ARGS[@]}" --load \
  -t mediary-scout-web:latest "${BUILD_ARGS[@]}" "$UPSTREAM_DIR"

echo "==> 构建 mediary-qqbot"
docker buildx build "${PLATFORM_ARGS[@]}" --load -t mediary-qqbot:latest ./qqbot

echo "==> 构建 mediary-strm-sync"
docker buildx build "${PLATFORM_ARGS[@]}" --load -t mediary-strm-sync:latest ./strm-sync

if [[ -n "$SAVE_PATH" ]]; then
  echo "==> 导出镜像到 $SAVE_PATH"
  SAVE_ARGS=()
  [[ -n "$PLATFORM" ]] && SAVE_ARGS=(--platform "$PLATFORM")
  docker save "${SAVE_ARGS[@]}" -o "$SAVE_PATH" \
    mediary-scout-web:latest mediary-qqbot:latest mediary-strm-sync:latest \
    postgres:16-alpine openlistteam/openlist:latest \
    ghcr.nju.edu.cn/fish2018/pansou-web:latest
  echo "    完成：$(du -h "$SAVE_PATH" | cut -f1)"
fi

echo "==> 全部完成"
