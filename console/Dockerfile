# The console, built for Cloud Run. Spec 6.9, 19.
#
# `next.config.mjs` sets `output: 'standalone'`, so the build emits a server plus
# exactly the node_modules it traced. That is what makes the final stage small
# and it is why this is a Dockerfile rather than a buildpack: buildpacks run
# `npm start`, which runs `next start`, which wants the whole dev tree.
#
# Nothing is baked in, including the NEXT_PUBLIC_ values, and that is not the
# usual answer for a Next app. Next inlines `process.env.NEXT_PUBLIC_X` into the
# bundle at build time by static text replacement, but `src/lib/env.ts` reads
# `process.env[name]` with `name` a variable, which is what makes it a boundary
# and is also unreplaceable. So every variable is read from the real environment
# at request time on the server, and the client receives the Firebase config as a
# prop rather than reading any environment of its own.
#
# First deploy passed them as build arguments and the running console reported no
# Firebase config at all, with every value correctly set on the image.

FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# The build renders pages, which reaches the env boundary, which requires
# GOOGLE_CLOUD_PROJECT. A placeholder is correct: the value that matters is the
# one on the service at request time.
ENV GOOGLE_CLOUD_PROJECT=build-time-placeholder
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
# Non-root. The container reads Firestore with the service account's own
# credentials from the metadata server and has no reason to own its filesystem.
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001
# No `public/` copy: this app has none. A COPY of a directory that does not
# exist fails the build, and creating an empty one to satisfy the line would be
# a directory that exists only to be copied.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
USER nextjs
# Cloud Run sets PORT. Binding 0.0.0.0 rather than localhost, or the container
# starts, passes its own health check locally, and is unreachable from outside.
ENV PORT=8080
ENV HOSTNAME=0.0.0.0
EXPOSE 8080
CMD ["node", "server.js"]
