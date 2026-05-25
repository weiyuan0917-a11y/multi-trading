#!/usr/bin/env bash
# Next.js 开发模式；日志写入 logs/frontend-*.log
set -euo pipefail
EDITION="${NEXT_PUBLIC_MT_EDITION:-user}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --edition)
      EDITION="${2:-user}"
      shift 2
      ;;
    --edition=*)
      EDITION="${1#*=}"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done
if [[ "$EDITION" != "user" && "$EDITION" != "admin" ]]; then
  echo "Edition must be user or admin" >&2
  exit 2
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/logs"
TS="$(date +%Y%m%d-%H%M%S)"
LOG="$ROOT/logs/frontend-${TS}.log"
cd "$ROOT/frontend"
echo "frontend npm run dev | edition: $EDITION | log: $LOG"
export PORT="${PORT:-3010}"
export NEXT_PUBLIC_MT_EDITION="$EDITION"
npm run dev 2>&1 | tee -a "$LOG"
