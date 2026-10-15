"use client";

/**
 * The frame for every signed-in page: checks the user may be here
 * (signed in and onboarded), then shows the sidebar, the top bar and the
 * page content.
 *
 *   <AppShell wide>...</AppShell>             wide = more room, for item grids
 *   <AppShell require="staff">...</AppShell>  guards and the admin only
 *   <AppShell require="admin">...</AppShell>  the admin only
 *
 * The role check here only decides what to show. The real protection is
 * on the backend: every staff and admin endpoint checks the role again.
 */

import Link from "next/link";

import { useAuth, useRequireUser } from "@/components/AuthProvider";
import Sidebar from "@/components/Sidebar";
import Topbar from "@/components/Topbar";
import styles from "./AppShell.module.css";

const ALLOWED = {
  staff: ["GUARD", "ADMIN"],
  admin: ["ADMIN"],
};

export default function AppShell({ children, wide = false, require = null }) {
  const { error } = useAuth();
  const user = useRequireUser();

  if (error) {
    return (
      <main className="page">
        <div className="alert" role="alert">
          {error}
        </div>
      </main>
    );
  }
  if (!user) return <p className="loading">Loading…</p>;

  const allowed = !require || ALLOWED[require].includes(user.role);

  return (
    <div className={styles.shell}>
      <Sidebar user={user} />
      <div className={styles.column}>
        <Topbar user={user} />
        <main className={wide ? "page page-wide" : "page"}>
          {allowed ? (
            children
          ) : (
            <>
              <h1>Not available</h1>
              <p className="lede">
                {require === "admin"
                  ? "Only the admin can open this page."
                  : "Only campus security staff can open this page."}
              </p>
              <Link href="/">Go to the home page</Link>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
