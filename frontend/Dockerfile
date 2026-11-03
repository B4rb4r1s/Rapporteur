FROM node:22-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da

# Зависимости ставятся в образ на уровень выше исходников: исходники монтируются в /repo/frontend,
# а Node и TypeScript находят пакеты в /repo/node_modules, поднимаясь по каталогам.
WORKDIR /repo
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund && chmod -R a+rX /repo/node_modules

ENV PATH=/repo/node_modules/.bin:$PATH
WORKDIR /repo/frontend
