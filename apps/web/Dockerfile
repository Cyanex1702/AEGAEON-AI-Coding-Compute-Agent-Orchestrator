FROM node:22-alpine AS dependencies
WORKDIR /app
COPY apps/web/package*.json ./
RUN npm install

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=dependencies /app/node_modules ./node_modules
COPY apps/web ./
ARG NEXT_PUBLIC_AEGAEON_API_URL=http://127.0.0.1:8000
ARG NEXT_PUBLIC_AEGAEON_WS_URL=ws://127.0.0.1:8000
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/node_modules ./node_modules
EXPOSE 3000
CMD ["npm", "start"]

