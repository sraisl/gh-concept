#!/usr/bin/env bash
# Build and exercise the actual runtime image. Requires Docker, curl and jq.
set -euo pipefail

cd "$(dirname "$0")/.."
image=${IMAGE_NAME:-gh-concept:container-test}
container=
cleanup() {
  result=$?
  if [ -n "${container}" ]; then
    if [ "${result}" -ne 0 ]; then
      docker logs "${container}" >&2 || true
    fi
    docker rm -f "${container}" >/dev/null
  fi
  exit "${result}"
}
trap cleanup EXIT

docker build --tag "${image}" \
  --label "org.opencontainers.image.source=${IMAGE_SOURCE:-https://github.com/sraisl/gh-concept}" \
  --label "org.opencontainers.image.revision=${IMAGE_REVISION:-$(git rev-parse HEAD)}" \
  --label "org.opencontainers.image.version=${IMAGE_VERSION:-dev}" .
test "$(docker image inspect --format '{{.Config.User}}' "${image}")" = "65532:65532"

for port in 8080 9090; do
  args=(--detach --read-only --cap-drop ALL --security-opt no-new-privileges)
  if [ "${port}" = 9090 ]; then
    args+=(--env PORT=9090)
  fi
  container=$(docker run "${args[@]}" --publish "127.0.0.1::${port}" "${image}")
  address=$(docker port "${container}" "${port}/tcp")
  url="http://${address}"
  ready=false
  for ((attempt = 0; attempt < 50; attempt++)); do
    if curl --fail --silent --max-time 1 "${url}/healthz" > /dev/null; then
      ready=true
      break
    fi
    sleep 0.1
  done
  if [ "${ready}" != true ]; then
    echo "Container did not become ready on port ${port}" >&2
    exit 1
  fi

  curl --fail --silent --show-error --max-time 5 "${url}/healthz" | jq -e '. == {status: "ok"}'
  curl --fail --silent --show-error --max-time 5 "${url}/hello" | jq -e '. == {message: "hello, world"}'
  curl --fail --silent --show-error --max-time 5 "${url}/hello?name=container" | jq -e '. == {message: "hello, container"}'
  test "$(curl --silent --show-error --max-time 5 --output /dev/null --write-out '%{http_code}' --request POST "${url}/hello")" = 405
  test "$(curl --silent --show-error --max-time 5 --output /dev/null --write-out '%{http_code}' "${url}/missing")" = 404

  docker rm -f "${container}" >/dev/null
  container=
  echo "Container checks passed on port ${port}"
done
