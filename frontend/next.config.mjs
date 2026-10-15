/**
 * Next.js settings.
 *
 * The proxy below is the key idea of this frontend:
 * the browser only ever talks to this Next.js server (localhost:3000).
 * Any request to /api/... is forwarded to the FastAPI backend.
 *
 *   browser ──► localhost:3000/api/auth/me ──► 127.0.0.1:8000/auth/me
 *
 * Because the browser sees a single address, the session cookie set by
 * FastAPI belongs to localhost:3000 and is sent back automatically.
 * No cross-site cookie rules or CORS headers are involved.
 */

const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/:path*` }];
  },
};

export default nextConfig;
