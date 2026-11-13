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
  // Don't advertise the framework ("X-Powered-By: Next.js").
  poweredByHeader: false,
  // Security headers on every page. frame-ancestors / X-Frame-Options stop
  // other sites from showing our pages in a frame (clickjacking); nosniff
  // stops the browser guessing file types; Referrer-Policy keeps our URLs
  // private when a student follows a link to another site.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'" },
          { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/:path*` }];
  },
};

export default nextConfig;
