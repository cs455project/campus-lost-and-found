/**
 * The TraceBack mark: a dotted trail (where something was lost) leading back
 * to a location pin (where it's waiting). Drawn for this project, so the
 * team owns it; the same drawing is app/icon.svg (browser tab icon) and the
 * PNG in backend/app/static (emails).
 */

import { useId } from "react";

export default function LogoMark({ size = 40, className }) {
  const gradient = `tb-${useId().replace(/:/g, "")}`; // unique per use on a page
  return (
    <svg className={className} viewBox="0 0 64 64" width={size} height={size} role="img" aria-label="TraceBack logo">
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#3a52e0" />
          <stop offset="1" stopColor="#1e2e96" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill={`url(#${gradient})`} />
      {/* The trail: from where it was lost (green dot), curving back... */}
      <path
        d="M12 52 C 14 40, 30 46, 30 36 S 22 26, 30 22"
        fill="none"
        stroke="#ffffff"
        strokeOpacity="0.75"
        strokeWidth="3.8"
        strokeLinecap="round"
        strokeDasharray="0.1 6.2"
      />
      <circle cx="12" cy="52" r="3.4" fill="#7dd8b0" />
      {/* ...to the pin: where it's waiting. */}
      <path d="M42 8c-7.7 0-14 6.1-14 13.7C28 31.6 42 44 42 44s14-12.4 14-22.3C56 14.1 49.7 8 42 8z" fill="#ffffff" />
      <circle cx="42" cy="21.5" r="5.2" fill="#2c41c4" />
    </svg>
  );
}
