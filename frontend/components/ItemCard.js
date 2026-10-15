/**
 * One item as a "tag" in a grid (Found, Lost, Returned, My reports, Home).
 * The whole card links to the item's page. A small tilted stamp in the
 * corner says LOST or FOUND, like a rubber stamp on a lost-property tag.
 */

import { Building2, Clock, ImageOff, MapPin } from "lucide-react";
import Link from "next/link";

import { photoUrl } from "@/lib/api";
import { FINISHED_STATUSES, STATUS_LABELS, daysAgo } from "@/lib/labels";
import styles from "./ItemCard.module.css";

// fadeFinished: grey out returned/closed items (useful in My reports, where
// they're mixed with open ones; not on the Returned page, where all are).
export default function ItemCard({ item, showStatus = false, extraAction = null, fadeFinished = true }) {
  const isFound = item.kind === "FOUND";
  const finished = fadeFinished && FINISHED_STATUSES.includes(item.status);
  const stamp = item.status === "RETURNED" ? "Returned" : isFound ? "Found" : "Lost";

  return (
    <article className={`${styles.card} ${finished ? styles.finished : ""}`}>
      <Link href={`/items/${item.id}`} className={styles.link}>
        <div className={styles.photo}>
          {item.cover_photo_id ? (
            // A plain <img>: photos come from our own API, which checks permissions.
            // eslint-disable-next-line @next/next/no-img-element
            <img src={photoUrl(item.id, item.cover_photo_id)} alt="" loading="lazy" />
          ) : (
            <span className={styles.noPhoto}>
              <ImageOff size={22} strokeWidth={1.5} aria-hidden="true" />
              {item.category.name}
            </span>
          )}
          <span className={styles.stamp} data-kind={item.status === "RETURNED" ? "RETURNED" : item.kind}>
            {stamp}
          </span>
        </div>
        <div className={styles.body}>
          <span className={styles.ref}>{item.ref}</span>
          <h3 className={styles.title}>{item.title}</h3>
          {showStatus && <span className={`pill ${styles.status}`}>{STATUS_LABELS[item.status]}</span>}
          <p className={styles.meta}>
            <span>
              <MapPin size={13} strokeWidth={1.8} aria-hidden="true" />
              {item.location_text}
            </span>
            <span>
              <Clock size={13} strokeWidth={1.8} aria-hidden="true" />
              {isFound ? "Found" : "Lost"} {daysAgo(item.happened_on)}
            </span>
            {isFound && item.current_office && (
              <span>
                <Building2 size={13} strokeWidth={1.8} aria-hidden="true" />
                {item.current_office.name}
              </span>
            )}
          </p>
        </div>
      </Link>
      {extraAction && <div className={styles.action}>{extraAction}</div>}
    </article>
  );
}
