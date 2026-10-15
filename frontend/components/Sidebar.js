"use client";

/**
 * The left sidebar: brand, navigation and the "Report an item" button.
 * On phones (760px or narrower) it turns into a bar along the top with
 * icons only (the labels stay readable to screen readers).
 *
 * Links are shown by role. Hiding a link is only for tidiness: the backend
 * checks the role again on every staff and admin request.
 */

import {
  BadgeCheck,
  ClipboardList,
  House,
  Package,
  Plus,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  Tag,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import LogoMark from "@/components/Logo";
import styles from "./Sidebar.module.css";

const SECTIONS = [
  // Home always stays at the very top, for everyone.
  { title: null, links: [{ href: "/", label: "Home", icon: House }] },
  // Then the staff sections: guards and admins mostly work from these.
  // Students don't see them (filtered by role).
  {
    title: "Security",
    roles: ["GUARD", "ADMIN"],
    links: [
      { href: "/staff/claims", label: "Claims desk", icon: ClipboardList },
      { href: "/staff", label: "Staff desk", icon: ShieldCheck },
    ],
  },
  {
    title: "Admin",
    roles: ["ADMIN"],
    links: [{ href: "/admin/staff", label: "Manage staff", icon: Users }],
  },
  {
    title: "Browse",
    links: [
      { href: "/found", label: "Found items", icon: Package },
      { href: "/lost", label: "Lost items", icon: Search },
      { href: "/returned", label: "Returned", icon: RotateCcw },
    ],
  },
  {
    title: "Yours",
    links: [
      { href: "/my-reports", label: "My reports", icon: Tag },
      { href: "/my-claims", label: "My claims", icon: BadgeCheck },
    ],
  },
];

/** Which link is the current page? The longest match wins, so on
 * /staff/claims "Claims desk" is marked, not "Staff desk" (/staff).
 * Home ("/") only matches itself. */
function currentHref(pathname, links) {
  const matches = links.filter(
    (link) => pathname === link.href || (link.href !== "/" && pathname.startsWith(`${link.href}/`)),
  );
  return matches.sort((a, b) => b.href.length - a.href.length)[0]?.href;
}

export default function Sidebar({ user }) {
  const pathname = usePathname();
  const sections = SECTIONS.filter((section) => !section.roles || section.roles.includes(user.role));
  const current = currentHref(pathname, sections.flatMap((section) => section.links));

  return (
    <aside className={styles.sidebar}>
      <Link href="/" className={styles.brand}>
        <LogoMark size={40} className={styles.mark} />
        <span className={styles.brandText}>
          <span className={styles.brandName}>TraceBack</span>
          <span className={styles.brandSub}>IIT Kanpur Lost &amp; Found</span>
        </span>
      </Link>

      <nav className={styles.nav} aria-label="Main">
        {sections.map((section) => (
          <div key={section.title || "home"} className={styles.section}>
            {section.title && <span className={styles.sectionTitle}>{section.title}</span>}
            {section.links.map(({ href, label, icon: Icon }) => (
              <Link
                key={href}
                href={href}
                className={styles.link}
                aria-current={href === current ? "page" : undefined}
                title={label}
              >
                <Icon size={18} strokeWidth={1.7} aria-hidden="true" />
                <span className={styles.label}>{label}</span>
              </Link>
            ))}
          </div>
        ))}
      </nav>

      <Link href="/report" className={`button ${styles.report}`} title="Report an item">
        <Plus size={18} strokeWidth={2.2} aria-hidden="true" />
        <span className={styles.label}>Report an item</span>
      </Link>

      <div className={styles.next}>
        <Sparkles size={16} strokeWidth={1.8} aria-hidden="true" />
        <div>
          <strong>AI matching is on</strong>
          <p>Similar lost and found items appear on your reports automatically.</p>
        </div>
      </div>
    </aside>
  );
}
