"use client";

/**
 * Home page for signed-in users.
 *
 * Just two sections: the newest lost reports and the newest found items,
 * each with a "See all" link to its full page (/lost and /found).
 */

import { ArrowRight, PackageOpen } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import ItemCard from "@/components/ItemCard";
import { apiFetch } from "@/lib/api";
import gridStyles from "@/components/ItemBrowser.module.css";
import styles from "./home.module.css";

/** How many items each section shows (two rows on a wide screen). */
const HOW_MANY = 8;

export default function HomePage() {
  return (
    <AppShell wide>
      <Home />
    </AppShell>
  );
}

/** Load one endpoint; on failure keep null so the page still shows the rest. */
function useData(path, query) {
  const [data, setData] = useState(null);
  const key = JSON.stringify(query);
  useEffect(() => {
    apiFetch(path, { query: JSON.parse(key) })
      .then(setData)
      .catch(() => setData(null));
  }, [path, key]);
  return data;
}

function Home() {
  const lost = useData("/items", { kind: "LOST", page_size: HOW_MANY });
  const found = useData("/items", { kind: "FOUND", page_size: HOW_MANY });

  return (
    <div className={styles.main}>
      <Recent
        title="Recently reported lost"
        href="/lost"
        linkText="See all lost items"
        result={lost}
        empty="Nobody is looking for anything right now."
      />
      <Recent
        title="Recently reported found"
        href="/found"
        linkText="See all found items"
        result={found}
        empty="Nothing has been handed in yet."
      />
    </div>
  );
}

function Recent({ title, href, linkText, result, empty }) {
  return (
    <section aria-label={title}>
      <div className={styles.sectionHead}>
        <h2>{title}</h2>
        <Link href={href} className={styles.seeAll}>
          {linkText} <ArrowRight size={15} aria-hidden="true" />
        </Link>
      </div>
      {!result ? (
        <p className="loading">Loading…</p>
      ) : result.items.length === 0 ? (
        <div className={gridStyles.empty}>
          <PackageOpen size={26} strokeWidth={1.5} aria-hidden="true" />
          <p>{empty}</p>
        </div>
      ) : (
        <div className={gridStyles.grid}>
          {result.items.map((item) => (
            <ItemCard key={item.id} item={item} />
          ))}
        </div>
      )}
    </section>
  );
}
