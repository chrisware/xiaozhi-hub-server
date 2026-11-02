#!/usr/bin/env bash
# xiaozhi-hub-server 远程一键安装（macOS / Linux）
# 用法：curl -fsSL https://raw.githubusercontent.com/chrisware/xiaozhi-hub-server/english/image-from-forks/scripts/bootstrap.sh | bash
#   下载代码到 ~/xiaozhi-hub-server（只下载运行需要的目录），然后运行 scripts/install.sh。
#   已经装过时，改为运行 scripts/update.sh 更新。
# 可选环境变量：
#   XIAOZHI_HUB_DIR     安装目录，默认 ~/xiaozhi-hub-server
#   XIAOZHI_HUB_REPO    仓库地址，默认 https://github.com/chrisware/xiaozhi-hub-server.git（英文分叉；上游用 justhalfbit/*）
#   XIAOZHI_HUB_BRANCH  分支，默认 english/image-from-forks（并入 main 后改回 main）
#   以及 install.sh 支持的 UV_INDEX_URL、HF_ENDPOINT、ASSUME_YES
set -euo pipefail

# 整个脚本放在函数里、最后一行才调用：curl 下载中断时，bash 不会执行半截脚本
main() {
  local dir="${XIAOZHI_HUB_DIR:-$HOME/xiaozhi-hub-server}"
  local repo="${XIAOZHI_HUB_REPO:-https://github.com/chrisware/xiaozhi-hub-server.git}"
  local branch="${XIAOZHI_HUB_BRANCH:-english/image-from-forks}"
  # 只检出运行需要的目录（根目录的文件总会检出）。改动时 update.sh 里的 SPARSE_PATHS 要一起改
  local sparse_paths=(main/xiaozhi-server scripts hub-docs)

  local os
  os="$(uname -s)"
  [[ "$os" == "Darwin" || "$os" == "Linux" ]] || fail "不支持的系统：${os}（Windows 请手动下载代码后运行 scripts/install.ps1）"

  step "检查 git"
  if ! git --version >/dev/null 2>&1; then
    if [[ "$os" == "Darwin" ]]; then
      fail "未找到 git。请先运行 xcode-select --install 安装命令行工具，装好后重新运行本命令"
    fi
    fail "未找到 git。请先用系统的包管理器安装 git（例如 sudo apt-get install git），再重新运行本命令"
  fi
  info "$(git --version)"

  if [[ -e "$dir" ]]; then
    if git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 \
      && [[ "$(git -C "$dir" remote get-url origin 2>/dev/null || true)" == *xiaozhi-hub-server* ]]; then
      # 已经装过（或上次安装中途失败）：update.sh 拉取新代码，并在安装没完成时接着装完
      [[ -f "$dir/scripts/update.sh" ]] || fail "${dir} 是旧版本，还没有更新脚本。请先运行 git -C \"$dir\" pull，再重新运行本命令"
      step "已经安装在 ${dir}，改为更新到最新版本"
      run_with_tty bash "$dir/scripts/update.sh"
      return
    fi
    [[ -d "$dir" && -z "$(ls -A "$dir")" ]] || fail "${dir} 已存在且不是本项目。请换个目录：XIAOZHI_HUB_DIR=<目录> 再运行本命令"
  fi

  step "下载代码到 ${dir}"
  # sparse checkout 需要 git 2.25 以上；更老的版本退回到完整检出（多下载约 200MB）
  # 下载失败时删掉下了一半的目录，否则重新运行本命令会被当成"目录已存在"
  if git_at_least 2 25; then
    info "只下载 ${sparse_paths[*]}（不含上游智控台、数字人等用不到的目录和提交历史）"
    { git clone --depth 1 --filter=blob:none --sparse --branch "$branch" "$repo" "$dir" \
      && git -C "$dir" sparse-checkout set "${sparse_paths[@]}"; } \
      || { rm -rf "$dir"; fail "代码下载失败，请检查能否访问 GitHub 后重新运行本命令"; }
  else
    info "git 版本较老，不支持只下载部分目录，将下载完整代码（不含提交历史）"
    git clone --depth 1 --branch "$branch" "$repo" "$dir" \
      || { rm -rf "$dir"; fail "代码下载失败，请检查能否访问 GitHub 后重新运行本命令"; }
  fi

  step "运行安装脚本"
  run_with_tty bash "$dir/scripts/install.sh"
}

step() { printf "\n\033[1m==> %s\033[0m\n" "$*"; }
info() { printf "    %s\n" "$*"; }
fail() { printf "\n\033[31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

# git_at_least <主版本> <次版本>
git_at_least() {
  local v major minor
  v="$(git --version | sed -E 's/[^0-9]*([0-9]+)\.([0-9]+).*/\1 \2/')"
  read -r major minor <<<"$v"
  (( major > $1 || (major == $1 && minor >= $2) ))
}

# 通过 curl | bash 运行时，标准输入是脚本本身。安装过程中的询问改从终端读取；没有终端时按默认回答
run_with_tty() {
  if [[ -t 0 ]]; then "$@"
  elif (: </dev/tty) 2>/dev/null; then "$@" </dev/tty
  else "$@"
  fi
}

main "$@"
