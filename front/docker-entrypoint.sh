#!/bin/sh
set -e

# Generate env.js with runtime environment variables
cat <<EOF > /usr/share/nginx/html/env.js
window.__ENV__ = {
  BACKEND_HOST: "${BACKEND_HOST:-}",
  TITLE: "${TITLE:-}"
};
EOF

# Execute the CMD (nginx)
exec "$@"
