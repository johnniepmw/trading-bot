# syntax=docker/dockerfile:1
# Runtime-only image: the app uses Node built-ins and runs TypeScript directly via Node's
# type stripping, so no node_modules or build step are needed. If you add runtime
# dependencies, add a build stage with `pnpm install --prod --frozen-lockfile`.
FROM node:24-slim

ENV NODE_ENV=production \
    PORT=8080
WORKDIR /app

COPY --chown=node:node package.json flags.json ./
COPY --chown=node:node src ./src

ARG GIT_SHA=dev
ENV APP_VERSION=${GIT_SHA}

USER node
EXPOSE 8080
CMD ["node", "--disable-warning=ExperimentalWarning", "src/server.ts"]
