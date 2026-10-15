/**
 * The root layout wraps every page. Anything here (fonts, global styles,
 * the AuthProvider) applies to the whole site.
 *
 * Fonts are installed from npm (@fontsource), so the site never has to
 * call Google Fonts: they're served from our own server.
 *   Bricolage Grotesque  headings and the brand name
 *   Inter                body text
 *   JetBrains Mono       references (FND-0031), labels, stamps, numbers
 */

import "@fontsource/bricolage-grotesque/600.css";
import "@fontsource/bricolage-grotesque/700.css";
import "@fontsource/bricolage-grotesque/800.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/500.css";
import "@fontsource/inter/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/700.css";
import "./globals.css";

import { AuthProvider } from "@/components/AuthProvider";

export const metadata = {
  title: "TraceBack · IIT Kanpur Lost & Found",
  description: "TraceBack: report, find and collect lost items through IIT Kanpur security offices.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
