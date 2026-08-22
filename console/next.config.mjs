/** @type {import('next').NextConfig} */
const nextConfig = {
  // Cloud Run serves this as a standalone server. Spec 6.9.
  output: 'standalone',
  reactStrictMode: true,
  // The console reads Firestore directly and writes through one API route.
  // Nothing here is statically exportable, and pretending otherwise would
  // silently serve a stale fleet board.
  experimental: { typedRoutes: true },
}

export default nextConfig
