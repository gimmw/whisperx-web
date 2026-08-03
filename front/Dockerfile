# Stage 1: Build the Svelte/Vite frontend
FROM node:22-alpine AS build

WORKDIR /app

# Copy dependency manifests first for better layer caching
COPY package.json yarn.lock ./

# Install dependencies
RUN yarn install --frozen-lockfile

# Copy source files needed for the build
COPY index.html vite.config.ts svelte.config.js tsconfig.json tsconfig.node.json ./
COPY src/ src/

# Build production assets
RUN yarn build

# Stage 2: Serve with unprivileged nginx
FROM nginxinc/nginx-unprivileged:stable-alpine

# Add nginx config for SPA routing (all paths -> index.html)
COPY front/nginx.conf /etc/nginx/conf.d/default.conf

# Switch to root to copy assets and set permissions
USER root

# Copy built assets from the build stage
COPY --from=build /app/dist /usr/share/nginx/html

# Create a placeholder env.js writable by the unprivileged nginx user
RUN touch /usr/share/nginx/html/env.js && \
    chmod 666 /usr/share/nginx/html/env.js

# Add entrypoint script that generates env.js from environment variables
COPY front/docker-entrypoint.sh /docker-entrypoint.sh

# Switch back to the unprivileged nginx user
USER nginx

EXPOSE 8080

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
