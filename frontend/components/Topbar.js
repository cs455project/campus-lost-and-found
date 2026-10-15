"use client";

/**
 * The bar across the top of every signed-in page: a search box (searches
 * the Found page), and who's signed in, with Sign out.
 */

import { LogOut, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import styles from "./Topbar.module.css";

const ROLE_NAMES = { STUDENT: "Student", GUARD: "Security guard", ADMIN: "Admin" };

/** "Asha Verma" -> "AV" for the round avatar. */
function initials(name) {
  return (name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

export default function Topbar({ user }) {
  const { signOut } = useAuth();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [signingOut, setSigningOut] = useState(false);

  function search(event) {
    event.preventDefault();
    const text = query.trim();
    router.push(text ? `/found?q=${encodeURIComponent(text)}` : "/found");
  }

  async function handleSignOut() {
    setSigningOut(true);
    try {
      await signOut();
      router.replace("/login");
    } finally {
      setSigningOut(false);
    }
  }

  return (
    <header className={styles.topbar}>
      <form className={styles.search} role="search" onSubmit={search}>
        <Search size={17} strokeWidth={1.8} aria-hidden="true" className={styles.searchIcon} />
        <label htmlFor="top-search" className="visually-hidden">
          Search found items
        </label>
        <input
          id="top-search"
          type="search"
          placeholder="Search found items, brands, places…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </form>

      <div className={styles.account}>
        <span className={styles.avatar} aria-hidden="true">
          {initials(user.full_name)}
        </span>
        <span className={styles.who}>
          <span className={styles.name}>{user.full_name || user.email}</span>
          <span className={styles.meta}>{user.roll_number || ROLE_NAMES[user.role]}</span>
        </span>
        <button
          type="button"
          className={styles.signOut}
          onClick={handleSignOut}
          disabled={signingOut}
          title="Sign out"
        >
          <LogOut size={17} strokeWidth={1.8} aria-hidden="true" />
          <span className="visually-hidden">Sign out</span>
        </button>
      </div>
    </header>
  );
}
