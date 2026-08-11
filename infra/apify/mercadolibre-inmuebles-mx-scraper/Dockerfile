FROM apify/actor-node-playwright-chrome:20

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --omit=optional \
    && npm cache clean --force

COPY . ./
CMD npm start
