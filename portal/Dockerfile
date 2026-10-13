# Portal: build Vite -> nginx sirviendo estatico y proxy /api -> servicio api (Railway: usa la red privada)
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
# compuerta: si nginx.conf tiene un error, falla el BUILD (el despliegue anterior sigue vivo) y no el arranque
RUN API_UPSTREAM=http://127.0.0.1:8000 PORT=8080 envsubst '${API_UPSTREAM} ${PORT}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf \
  && nginx -t \
  && rm /etc/nginx/conf.d/default.conf
ENV API_UPSTREAM=http://api:8000 PORT=80
EXPOSE 80
