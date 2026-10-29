#!/usr/bin/env bash
# Run a Kaisen project bundle in the runtime image locally, the way PicoPaaS runs a preview.
#   runtime/smoke-local.sh path/to/project.tar.gz      (the project's "Download .tar.gz")
# Needs Docker running (`open -a Docker` on macOS). Builds kaisen-runtime:local, serves the tarball on the host,
# starts the container as UID 10001 and waits for HTTP 200 on http://127.0.0.1:${HOST_PORT:-18080}/.
# Set PLATFORM=linux/amd64 to build for the cluster's architecture (slower under emulation on Apple silicon).
set -euo pipefail
bundle=${1:?usage: $0 project.tar.gz}
here=$(cd "$(dirname "$0")" && pwd)
host_port=${HOST_PORT:-18080}
serve_port=${SERVE_PORT:-8799}
work=$(mktemp -d)
trap 'docker rm -f kaisen-smoke >/dev/null 2>&1 || true; kill "${srv:-0}" 2>/dev/null && wait "$srv" 2>/dev/null; rm -rf "$work"' EXIT

docker build ${PLATFORM:+--platform "$PLATFORM"} -q -t kaisen-runtime:local "$here" >/dev/null
echo "image: $(docker image ls kaisen-runtime:local --format '{{.Size}}')"
cp "$bundle" "$work/b.tar.gz"
sha=$(shasum -a 256 "$work/b.tar.gz" | cut -d' ' -f1)
(cd "$work" && exec python3 -m http.server "$serve_port" --bind 0.0.0.0 >/dev/null 2>&1) & srv=$!

docker run -d --name kaisen-smoke --user 10001 -p "$host_port:8080" \
  -e KAISEN_BUNDLE_URL="http://host.docker.internal:$serve_port/b.tar.gz" -e KAISEN_BUNDLE_SHA256="$sha" \
  ${PLATFORM:+--platform "$PLATFORM"} kaisen-runtime:local >/dev/null

code=000
for _ in $(seq 1 120); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$host_port/" || true)
  [ "$code" = 200 ] && break
  docker ps -q -f name=kaisen-smoke | grep -q . || break  # container exited
  sleep 1
done
docker logs kaisen-smoke 2>&1 | tail -20
echo "HTTP $code from http://127.0.0.1:$host_port/"
[ "$code" = 200 ] || exit 1
if [ -n "${KEEP:-}" ]; then echo "KEEP set: container left running; press Enter to stop"; read -r _; fi
