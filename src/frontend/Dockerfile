# When building arm64, CI passes FRONTEND_IMAGE=<amd64-tag> so BuildKit
# reuses the already-built static output and skips the slow Node build
# under QEMU. For local / amd64 builds the default is used.
ARG FRONTEND_IMAGE=frontend-build-output

FROM node:24.21.0-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1 AS frontend-deps

# pnpm, provided by corepack (version pinned via package.json#packageManager).
RUN corepack enable
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
ENV HOME=/home/frontend

WORKDIR /home/frontend

# Install as the `node` user (uid/gid 1000), not root. With `umask 0002`
# every installed file is group-writable for gid 1000, so a runtime user
# in that group (compose runs the container as `<host uid>:1000`) can
# rewrite node_modules without a recursive chown. Owning the workdir up
# front is enough; everything created below inherits node ownership.
RUN chown node:node /home/frontend
USER node

COPY --chown=node:node ./package.json ./pnpm-workspace.yaml ./pnpm-lock.yaml ./

# --frozen-lockfile keeps the image build reproducible (fails on
# package.json/lockfile drift instead of silently rewriting the lockfile).
RUN umask 0002 && pnpm install --frozen-lockfile

COPY --chown=node:node . .

# ---- Development server (compose `frontend-dev`) ----
FROM frontend-deps AS contacts-dev

EXPOSE 8970

RUN pnpm run build-theme

CMD ["pnpm", "run", "dev"]

# ---- Production build ----
FROM frontend-deps AS contacts-builder

ARG API_ORIGIN
ENV VITE_API_ORIGIN=${API_ORIGIN}

RUN pnpm run build

# Scratch stage that normalises the build output path.
# When FRONTEND_IMAGE keeps its default value, BuildKit uses this stage.
FROM scratch AS frontend-build-output
COPY --from=contacts-builder /home/frontend/dist /app

# When FRONTEND_IMAGE is overridden to an external image tag, BuildKit
# pulls that image instead and skips every stage above.
FROM ${FRONTEND_IMAGE} AS frontend-source

FROM alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d AS tools-download
ARG TARGETARCH

ARG CADDY_VERSION=2.11.4
# When upgrading, run
# for arch in amd64 arm64; do printf "CADDY_SHA256_%s=%s\n" "$(echo $arch | tr a-z A-Z)" "$(wget -qO- "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_${arch}.tar.gz" | sha256sum | cut -d' ' -f1)"; done
# and update checksums accordingly (and bin/scalingo_postfrontend).
ARG CADDY_SHA256_AMD64=527fbf917c39189a1e3b31d34fa955601680b2d5c8055d2a87b8b9588dec7bb9
ARG CADDY_SHA256_ARM64=52d42ae12b3462097e9868da6dfed3c9648ae12edd3b3638102312af84cb6904
RUN wget -qO /tmp/caddy.tar.gz "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && case "${TARGETARCH}" in \
         amd64) echo "${CADDY_SHA256_AMD64}  /tmp/caddy.tar.gz" | sha256sum -c - ;; \
         arm64) echo "${CADDY_SHA256_ARM64}  /tmp/caddy.tar.gz" | sha256sum -c - ;; \
         *) echo "Unsupported architecture: ${TARGETARCH}" && exit 1 ;; \
       esac \
    && tar -xzf /tmp/caddy.tar.gz caddy \
    && mv caddy /usr/bin/caddy \
    && rm /tmp/caddy.tar.gz

ARG LPROBE_VERSION=v0.2.0
# When upgrading, run
# for arch in amd64 arm64; do printf "LPROBE_SHA256_%s=%s\n" "$(echo $arch | tr a-z A-Z)" "$(wget -qO- "https://github.com/fivexl/lprobe/releases/download/${LPROBE_VERSION}/lprobe-linux-${arch}" | sha256sum | cut -d' ' -f1)"; done
# and update checksums accordingly
ARG LPROBE_SHA256_AMD64=8b55ddb15c2d8c5e1773c73c1413b26c7041e5836dabdbcef5f806ba6b9d17a2
ARG LPROBE_SHA256_ARM64=3f31eee19167f23dc64c645021b75306956da245e5b9763c90abd12797d0a72a
RUN wget -qO /usr/bin/lprobe "https://github.com/fivexl/lprobe/releases/download/${LPROBE_VERSION}/lprobe-linux-${TARGETARCH}" \
    && case "${TARGETARCH}" in \
         amd64) echo "${LPROBE_SHA256_AMD64}  /usr/bin/lprobe" | sha256sum -c - ;; \
         arm64) echo "${LPROBE_SHA256_ARM64}  /usr/bin/lprobe" | sha256sum -c - ;; \
         *) echo "Unsupported architecture: ${TARGETARCH}" && exit 1 ;; \
       esac \
    && chmod +x /usr/bin/lprobe

# ---- Production image: Caddy on distroless, non-root ----
# Serves the frontend and proxies the backend paths (caddy/Caddyfile).
FROM gcr.io/distroless/static-debian13:nonroot@sha256:e2e927ec666bae08560abb3c55d0659eceabb657f56b6782ab500a9fc7f555e3 AS frontend-production

COPY --from=tools-download /usr/bin/caddy /usr/bin/caddy
COPY --from=tools-download /usr/bin/lprobe /usr/bin/lprobe
COPY --from=frontend-source /app /app
COPY caddy/Caddyfile /etc/caddy/Caddyfile

ENV PORT=8080
ENV CONTACTS_FRONTEND_ROOT=/app
ENV CONTACTS_FRONTEND_BACKEND_SERVER=localhost:8000
ENV DJANGO_ADMIN_URL=admin

ENTRYPOINT ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

# distroless has no shell to expand $PORT: keep this port in sync with ENV PORT.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD ["/usr/bin/lprobe", "-mode=http", "-port=8080", "-endpoint=/__lbheartbeat__"]
