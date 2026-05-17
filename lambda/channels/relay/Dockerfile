FROM node:22-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --production
COPY *.mjs ./
CMD ["node", "main.mjs"]
