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
RUN API_UPSTREAM=http://127.0.0.1:8000 PORT=8080 PROXY_SHARED_KEY=0123456789abcdef envsubst '${API_UPSTREAM} ${PORT} ${PROXY_SHARED_KEY}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf \
  && nginx -t \
  && rm /etc/nginx/conf.d/default.conf
# PROXY_SHARED_KEY (plan 1.10): la misma llave que la variable del api (hex, openssl rand -hex 32). Firma la IP real
# del cliente que nginx le manda al API. Vacia = no se manda y el API ve a TODOS con la IP de este nginx (limites por
# IP globales): obligatoria en Railway. Tiene que existir aunque sea vacia: la plantilla solo reemplaza las definidas.
ENV API_UPSTREAM=http://api:8000 PORT=80 PROXY_SHARED_KEY=""
# En Railway, sin llave (o corta) se avisa con ERROR en el log al arrancar. No se impide el arranque: tumbaria el panel
# y la tienda; los limites globales son el mal menor mientras se carga la variable.
RUN printf '%s\n' '#!/bin/sh' \
  'if [ -n "${RAILWAY_ENVIRONMENT_ID:-}${RAILWAY_ENVIRONMENT_NAME:-}" ] && [ "${#PROXY_SHARED_KEY}" -lt 32 ]; then' \
  '  echo "ERROR crimson: PROXY_SHARED_KEY vacia o corta en el portal: el API ve a todos con la IP de este nginx y los limites por IP son globales. Cargar la misma llave en portal y api (api/README.md)." >&2' \
  'fi' > /docker-entrypoint.d/15-crimson-proxy-key.sh \
  && chmod +x /docker-entrypoint.d/15-crimson-proxy-key.sh
EXPOSE 80
