# Stage 1: Build the Vite app
FROM node:20-alpine AS build
WORKDIR /app

# Build args are baked into the JS bundle at compile time. Pass via
# `docker build --build-arg VITE_API_BASE=... --build-arg VITE_DEFAULT_USER_ID=...`
# Default "" means frontend uses "/api" (relative path) — works with a reverse proxy.
ARG VITE_API_BASE=""
ARG VITE_DEFAULT_USER_ID=""
ENV VITE_API_BASE=$VITE_API_BASE \
    VITE_DEFAULT_USER_ID=$VITE_DEFAULT_USER_ID

COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

# Stage 2: Serve static files with nginx
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
