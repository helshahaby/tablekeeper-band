#!/usr/bin/env bash
# Build stage-1/ at an exact commit from a clean export, start it with -e PORT and a
# port mapping, time the first healthy response, then run the probe suite.
# Usage: run_review.sh <commit> [host_port]
set -euo pipefail
COMMIT="$1"
PORT="${2:-18123}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY=/home/hossam/tablekeeper-hack/dark-factory-wearedevs/.venv/bin/python
WORK="$(mktemp -d /tmp/tk-review-XXXXXX)"
TAG="tk-review:${COMMIT:0:12}"
NAME="tk-review-${COMMIT:0:12}"

git -C "$REPO" archive "$COMMIT" stage-1 | tar -x -C "$WORK"
echo "== exported $COMMIT to $WORK"
echo "== RUN.md:"; cat "$WORK/stage-1/RUN.md" || echo "(no RUN.md)"

docker build -t "$TAG" "$WORK/stage-1"
docker rm -f "$NAME" >/dev/null 2>&1 || true
start=$(date +%s.%N)
docker run -d --rm --name "$NAME" --cpus 2 --memory 2g -e PORT=9090 -p "$PORT:9090" "$TAG" >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 600); do
  if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi
  sleep 0.1
done
end=$(date +%s.%N)
echo "== first healthy response after $(echo "$end - $start" | bc) s"
"$PY" "$(dirname "$0")/probe.py" "http://127.0.0.1:$PORT" "${@:3}" || rc=$?
echo "== container stats:"; docker stats --no-stream "$NAME" || true
exit "${rc:-0}"
