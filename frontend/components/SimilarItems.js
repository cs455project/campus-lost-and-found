"use client";

/**
 * "Possible matches" on an item's page (AI Phase A, see backend/docs/AI.md).
 *
 * A lost report shows found items that might be it; a found item shows lost
 * reports that might be its owner's. Shown to the person who reported the
 * item, and staff (the backend says so with item.can_see_similar).
 *
 * The matching runs in the background after a report is saved, so at first
 * the backend answers status "pending". This component then shows "Looking
 * for similar items..." and asks again every few seconds until it's ready.
 */

import { Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { apiFetch, photoUrl } from "@/lib/api";
import { formatDate } from "@/lib/labels";
import listStyles from "@/app/staff/staff.module.css";
import styles from "./SimilarItems.module.css";

const POLL_MS = 3000;
const MAX_POLLS = 40; // stop asking after about 2 minutes

/** Words instead of a raw number: "0.83" means little to a student. */
function strength(score) {
  if (score >= 0.75) return { label: "Strong match", tone: "strong" };
  if (score >= 0.5) return { label: "Possible match", tone: "possible" };
  return { label: "Weak match", tone: "weak" };
}

/**
 * `version` changes whenever the item is edited, so the list is fetched
 * again (the backend re-runs matching after every edit).
 */
export default function SimilarItems({ item, version }) {
  const [result, setResult] = useState(null);

  useEffect(() => {
    let stopped = false;
    let timer = null;
    let polls = 0;

    async function load() {
      try {
        const data = await apiFetch(`/items/${item.id}/similar`);
        if (stopped) return;
        setResult(data);
        if (data.status === "pending" && polls < MAX_POLLS) {
          polls += 1;
          timer = setTimeout(load, POLL_MS);
        }
      } catch {
        if (!stopped) setResult(null); // on failure the section stays hidden
      }
    }

    load();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [item.id, version]);

  // Hidden until we know matching is switched on.
  if (!result || !result.enabled) return null;

  const isLost = item.kind === "LOST";
  // The lost report's owner can claim a suggested found item straight away.
  const canClaimFromHere = isLost && item.is_mine;

  let body;
  if (result.status === "pending") {
    body = (
      <p className={styles.pending} role="status">
        <span className={styles.spinner} aria-hidden="true" />
        Looking for similar {isLost ? "found items" : "lost reports"}… this takes a few seconds.
      </p>
    );
  } else if (result.status === "failed") {
    body = (
      <p className={styles.muted}>
        Couldn&apos;t check for matches right now. It will try again the next time this report changes.
      </p>
    );
  } else if (result.matches.length === 0) {
    body = (
      <p className={styles.muted}>
        Nothing similar yet. New {isLost ? "found items" : "lost reports"} are checked as they come in, so look again
        later.
      </p>
    );
  } else {
    body = (
      <>
        <p className={styles.muted}>
          {isLost
            ? "These found items look like yours. If one is, press “This is mine”."
            : "These lost reports might belong to the owner."}{" "}
          {result.matches.some((m) => m.method === "ai")
            ? "Ranked by the AI agent; it can be wrong, so check the details."
            : ""}
        </p>
        {/* Compact rows, not big cards: this sits inside a narrow item page. */}
        <ul className={listStyles.list}>
          {result.matches.map(({ item: other, score, reason, method }) => {
            const { label, tone } = strength(score);
            return (
              <li key={other.id} className={listStyles.row}>
                <div className={listStyles.thumb}>
                  {other.cover_photo_id ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={photoUrl(other.id, other.cover_photo_id)} alt="" loading="lazy" />
                  ) : (
                    <span>{other.category.name}</span>
                  )}
                </div>
                <div className={styles.body}>
                  <Link href={`/items/${other.id}`} className={listStyles.title}>
                    {other.title}
                  </Link>
                  <p className={listStyles.meta}>
                    {other.kind === "FOUND" ? "Found" : "Lost"} at {other.location_text} on{" "}
                    {formatDate(other.happened_on)}
                    {other.current_office ? `. Held at ${other.current_office.name}` : ""}.
                  </p>
                  <p className={styles.match}>
                    <span className={styles.strength} data-tone={tone}>
                      {label}
                    </span>
                    {method === "ai" && (
                      <span className={styles.aiTag} title="Ranked and explained by the AI agent">
                        AI
                      </span>
                    )}
                    {method === "idcard" && (
                      <span className={styles.idTag} title="The roll number or email on the item matches">
                        ID match
                      </span>
                    )}
                    {reason && <span className={styles.reason}>{reason}</span>}
                  </p>
                </div>
                {canClaimFromHere && other.kind === "FOUND" && (
                  <Link href={`/items/${other.id}/claim`} className={`button button-secondary ${styles.claim}`}>
                    This is mine
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      </>
    );
  }

  return (
    <section className={styles.section} aria-labelledby="similar-heading">
      <h2 id="similar-heading" className={styles.heading}>
        <Sparkles size={18} aria-hidden="true" /> Possible matches
      </h2>
      {body}
    </section>
  );
}
