FROM node:24-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 API_INTERNAL_URL=http://api:8000
COPY package*.json next.config.mjs ./
COPY app ./app
RUN --mount=type=secret,id=registry_ca \
    if [ -f /run/secrets/registry_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/registry_ca; fi; \
    npm ci && npm run build && cp -r .next/static .next/standalone/.next/static

FROM node:24-alpine
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/.next/standalone ./
EXPOSE 3000
CMD ["node", "server.js"]
