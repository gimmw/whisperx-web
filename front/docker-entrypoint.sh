#!/bin/sh
set -e

# Generate env.js with runtime environment variables.
#
# BACKEND_HOST defaults to /api, which nginx proxies to the backend (see
# nginx.conf). That keeps the browser on a single origin so CORS never applies.
# Override it with an absolute URL only if the backend is not reachable through
# this container's proxy — in which case the backend's CORS_ORIGINS must list
# this frontend's origin.
cat <<EOF > /usr/share/nginx/html/env.js
window.__ENV__ = {
  BACKEND_HOST: "${BACKEND_HOST:-/api}",
  TITLE: "${TITLE:-}"
};
EOF

# Execute the CMD (nginx)
exec "$@"
