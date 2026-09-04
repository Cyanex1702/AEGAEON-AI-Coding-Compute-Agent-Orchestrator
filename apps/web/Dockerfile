FROM node:22-alpine AS dependencies
WORKDIR /app
COPY apps/web/package*.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY apps/web ./
ARG NEXT_PUBLIC_AEGAEON_API_URL=/api/controller
ARG NEXT_PUBLIC_AEGAEON_WS_URL=ws://127.0.0.1:8000
ARG AEGAEON_CONTROLLER_URL=http://controller:8000
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --chown=node:node --from=builder /app/.next ./.next
COPY --chown=node:node --from=builder /app/package.json ./package.json
COPY --chown=node:node --from=builder /app/node_modules ./node_modules
USER node
EXPOSE 3000
CMD ["npm", "start"]

