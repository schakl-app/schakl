# Static site image — build from the REPO ROOT:
#   docker build -f apps/site/Dockerfile -t schakl-site .
FROM node:24-alpine AS build
RUN npm install -g pnpm@11.10.0
WORKDIR /repo
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY apps/site/package.json apps/site/
RUN pnpm install --frozen-lockfile --filter @schakl/site
COPY apps/site apps/site
RUN pnpm --filter @schakl/site build

FROM nginx:alpine
COPY --from=build /repo/apps/site/dist /usr/share/nginx/html
