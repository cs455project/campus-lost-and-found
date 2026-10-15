"use client";

/**
 * The staff desk (guards and the admin).
 *
 * Three tabs, one per stage of a found item:
 *   Waiting for drop-off  reported online, not handed in yet
 *   At offices            held at a security office (longest-held first)
 *   Closed                returned, disposed of or donated
 *
 * Like the Found page, the tab, office filter, search and page number live in
 * the address bar, so Back, refresh and bookmarks all work.
 */

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import { useAuth } from "@/components/AuthProvider";
import Pagination from "@/components/Pagination";
import { apiFetch, photoUrl } from "@/lib/api";
import { STATUS_LABELS, daysSince, formatDate } from "@/lib/labels";
import styles from "./staff.module.css";

const TABS = [
  { stage: "waiting", label: "Waiting for drop-off" },
  { stage: "held", label: "At offices" },
  { stage: "closed", label: "Closed" },
];

export default function StaffDeskPage() {
  return (
    <AppShell wide require="staff">
      <Suspense fallback={<p className="loading">Loading…</p>}>
        <StaffDesk />
      </Suspense>
    </AppShell>
  );
}

function StaffDesk() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const { user } = useAuth();

  const stage = TABS.some((t) => t.stage === params.get("stage")) ? params.get("stage") : "waiting";
  const office = params.get("office") || "";
  const query = params.get("q") || "";
  const page = Math.max(1, Number(params.get("page")) || 1);

  const [offices, setOffices] = useState([]);
  const [searchText, setSearchText] = useState(query);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    apiFetch("/offices").then(setOffices).catch(() => setOffices([]));
  }, []);

  useEffect(() => setSearchText(query), [query]);

  useEffect(() => {
    let cancelled = false;
    setResult(null);
    setError("");
    apiFetch("/staff/items", { query: { stage, office_id: office, q: query, page, page_size: 20 } })
      .then((data) => !cancelled && setResult(data))
      .catch((err) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [stage, office, query, page]);

  function hrefWith(changes) {
    const next = new URLSearchParams({ stage, office, q: query, page: String(page), ...changes });
    if (!("page" in changes)) next.set("page", "1");
    for (const [key, value] of [...next.entries()]) {
      if (!value || (key === "page" && value === "1") || (key === "stage" && value === "waiting")) next.delete(key);
    }
    const text = next.toString();
    return text ? `${pathname}?${text}` : pathname;
  }

  return (
    <>
      <div className={styles.heading}>
        <div>
          <h1>Staff desk</h1>
          <p className="lede">
            Found items at every security office.
            {user.office ? ` Your usual office is ${user.office.name}.` : ""}
          </p>
        </div>
        <Link href="/staff/walk-in" className="button">
          Register walk-in item
        </Link>
      </div>

      <nav className={styles.tabs} aria-label="Item stages">
        {TABS.map((tab) => (
          <Link
            key={tab.stage}
            href={hrefWith({ stage: tab.stage })}
            className={styles.tab}
            aria-current={tab.stage === stage ? "page" : undefined}
          >
            {tab.label}
          </Link>
        ))}
      </nav>

      <form
        className={styles.filters}
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          router.push(hrefWith({ q: searchText.trim() }));
        }}
      >
        <div className="field">
          <label htmlFor="office">Office</label>
          <select id="office" value={office} onChange={(e) => router.push(hrefWith({ office: e.target.value }))}>
            <option value="">All offices</option>
            {offices.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="q">Search</label>
          <input
            id="q"
            type="search"
            placeholder="Reference (FND-0031) or words"
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
        </div>
        <button type="submit" className="button">
          Search
        </button>
      </form>

      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {!result && !error && <p className="loading">Loading…</p>}

      {result && (
        <>
          <p className={styles.count} aria-live="polite">
            {result.total === 0 ? "" : `${result.total} item${result.total === 1 ? "" : "s"}`}
          </p>
          {result.items.length === 0 ? (
            <div className={styles.empty}>
              <p>
                {query || office
                  ? "No items match these filters."
                  : stage === "waiting"
                    ? "No finders are on their way. Items appear here when someone reports a found item online."
                    : stage === "held"
                      ? "No items are being held at the offices."
                      : "No items have been closed yet."}
              </p>
            </div>
          ) : (
            <ul className={styles.list}>
              {result.items.map((item) => (
                <StaffRow key={item.id} item={item} stage={stage} />
              ))}
            </ul>
          )}
          <Pagination
            page={result.page}
            totalPages={result.total_pages}
            hrefFor={(p) => hrefWith({ page: String(p) })}
          />
        </>
      )}
    </>
  );
}

function StaffRow({ item, stage }) {
  return (
    <li className={styles.row}>
      <div className={styles.thumb}>
        {item.cover_photo_id ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={photoUrl(item.id, item.cover_photo_id)} alt="" loading="lazy" />
        ) : (
          <span>{item.category.name}</span>
        )}
      </div>
      <div className={styles.main}>
        <Link href={`/items/${item.id}`} className={styles.title}>
          {item.title}
        </Link>
        <span className={styles.ref}>{item.ref}</span>
        <p className={styles.meta}>
          {stage === "waiting" && (
            <>
              Reported by {item.reporter.full_name}
              {item.reporter.roll_number ? ` (${item.reporter.roll_number})` : ""} on {formatDate(item.created_at)}.
              Coming to {item.dropoff_office?.name}.
            </>
          )}
          {stage === "held" && (
            <>
              At {item.current_office?.name}, held for {daysSince(item.received_at)} day
              {daysSince(item.received_at) === 1 ? "" : "s"}.
              {item.status === "CLAIM_PENDING" ? " Claim in progress." : ""}
            </>
          )}
          {stage === "closed" && (
            <>
              {STATUS_LABELS[item.status]} on {formatDate(item.updated_at)}.
            </>
          )}
        </p>
      </div>
      <Link href={`/items/${item.id}`} className={`button button-secondary ${styles.action}`}>
        {stage === "waiting" ? "Receive" : "Open"}
      </Link>
    </li>
  );
}
