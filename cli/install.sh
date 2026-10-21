#!/bin/sh
# MiniTV CLI installer.
#
#   curl -fsSL https://minitv.us/cli/install.sh | sh
#   curl -fsSL https://minitv.us/cli/install.sh | MINITV_API_KEY='mk_live_...' sh
#
# 行为：校验 python3 → 下载单文件到 ~/.local/bin/minitv → 可选写入 API Key →
# 自检。幂等：重复执行是覆盖升级，没给 MINITV_API_KEY 时不动已保存的密钥。
set -eu

BASE="${MINITV_CLI_BASE:-https://minitv.us/cli}"
PREFIX="${MINITV_BIN_DIR:-$HOME/.local/bin}"
DO_DOCTOR=1
MIN_PY="3.8"

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --prefix=*) PREFIX="${1#*=}"; shift ;;
    --no-doctor) DO_DOCTOR=0; shift ;;
    --base) BASE="$2"; shift 2 ;;
    --base=*) BASE="${1#*=}"; shift ;;
    -h|--help)
      cat <<'USAGE'
minitv install.sh

用法: sh install.sh [--prefix DIR] [--base URL] [--no-doctor]

  --prefix DIR   安装目录（默认 ~/.local/bin）
  --base URL     下载基址（默认 https://minitv.us/cli）
  --no-doctor    装完不跑 minitv doctor
  -h, --help     显示本说明

环境变量:
  MINITV_API_KEY 提供时自动写入（经 stdin，不进进程参数）；缺省则只安装。
USAGE
      exit 0
      ;;
    *) echo "未知参数: $1（--help 查看用法）" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
fail() { printf '[err] %s\n' "$*" >&2; exit 1; }

# ---- 1. python3 ----
PY=$(command -v python3 || true)
[ -n "$PY" ] || fail "没有找到 python3。先安装 Python ${MIN_PY}+（macOS: brew install python3；Debian: apt install python3）"
"$PY" - "$MIN_PY" <<'PYCHECK' || fail "python3 版本过低，需要 ${MIN_PY}+"
import sys
want = tuple(int(part) for part in sys.argv[1].split("."))
have = sys.version_info[:2]
sys.exit(0 if have >= want else 1)
PYCHECK
say "python3  $("$PY" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"

# ---- 2. download ----
mkdir -p "$PREFIX" || fail "无法创建目录 $PREFIX"
TMP="$PREFIX/.minitv.download.$$"
URL="$BASE/minitv"
say "下载      $URL"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$URL" -o "$TMP" || { rm -f "$TMP"; fail "下载失败：$URL"; }
elif command -v wget >/dev/null 2>&1; then
  wget -q "$URL" -O "$TMP" || { rm -f "$TMP"; fail "下载失败：$URL"; }
else
  fail "系统里没有 curl 或 wget"
fi
head -n 1 "$TMP" | grep -q "python" || { rm -f "$TMP"; fail "下载到的内容不是 minitv（检查 $BASE）"; }
chmod 0755 "$TMP"
mv "$TMP" "$PREFIX/minitv"
say "已安装    $PREFIX/minitv"

case ":$PATH:" in
  *":$PREFIX:"*) ;;
  *) say "提示      $PREFIX 不在 PATH 里，加到 shell 配置："
     say "          export PATH=\"$PREFIX:\$PATH\"" ;;
esac

MINITV="$PREFIX/minitv"

# ---- 3. key ----
if [ -n "${MINITV_API_KEY:-}" ]; then
  # stdin, not argv: the key must never show up in `ps`.
  if printf '%s' "$MINITV_API_KEY" | "$MINITV" config set-key --stdin; then
    say "密钥      已写入本地凭据文件"
  else
    fail "密钥校验失败，未写入（磁盘上的旧密钥保持不变）"
  fi
else
  say "密钥      未提供；稍后运行 $MINITV config set-key"
fi

# ---- 4. doctor ----
if [ "$DO_DOCTOR" = "1" ] && [ -n "${MINITV_API_KEY:-}" ]; then
  say ""
  "$MINITV" doctor || say "提示      doctor 未全部通过，按上面的提示处理"
fi

say ""
say "下一步    $MINITV guide     # 完整手册"
