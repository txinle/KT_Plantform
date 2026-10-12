FROM node:24-alpine AS build
WORKDIR /app/client
COPY client/package.json client/package-lock.json ./
RUN npm ci
COPY client/ ./
COPY contracts/ /app/contracts/
RUN npm run api:check && npm run build

FROM nginx:1.27-alpine AS production
COPY client/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/client/dist/spa-assets/ /static/spa-assets/
COPY --from=build /app/client/dist/spa-assets/assets/ /opt/kt-frontend-assets/
COPY --from=build /app/client/dist/spa-assets/capture-shell-*.html /opt/kt-frontend-shells/
COPY --from=build /app/client/legacy/js/ /static/js/
COPY deploy/docker/20-frontend-assets.sh /docker-entrypoint.d/20-frontend-assets.sh
RUN chmod +x /docker-entrypoint.d/20-frontend-assets.sh
