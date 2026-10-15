/**
 * A list of claims, one compact row each. Used by "My claims" (students)
 * and the Claims desk (staff). Staff also see who made each claim.
 */

import Link from "next/link";

import { photoUrl } from "@/lib/api";
import { CLAIM_STATUS_LABELS, formatDateTime } from "@/lib/labels";
import styles from "@/app/staff/staff.module.css";

export default function ClaimList({ claims, staffView = false }) {
  return (
    <ul className={styles.list}>
      {claims.map((claim) => (
        <li key={claim.id} className={styles.row}>
          <div className={styles.thumb}>
            {claim.item.cover_photo_id ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={photoUrl(claim.item.id, claim.item.cover_photo_id)} alt="" loading="lazy" />
            ) : (
              <span>{claim.item.category.name}</span>
            )}
          </div>
          <div>
            <Link href={`/claims/${claim.id}`} className={styles.title}>
              {claim.item.title}
            </Link>
            <span className={styles.ref}>
              {claim.ref}, item {claim.item.ref}
            </span>
            <p className={styles.meta}>
              {staffView && (
                <>
                  {claim.claimant_name}
                  {claim.claimant_roll ? ` (${claim.claimant_roll})` : ""}.{" "}
                </>
              )}
              {claim.on_hold ? "Waiting: another claim is approved" : CLAIM_STATUS_LABELS[claim.status]}
              {claim.status === "APPROVED" && claim.collect_by
                ? `, collect by ${formatDateTime(claim.collect_by)}`
                : `, sent ${formatDateTime(claim.created_at)}`}
              {claim.item.current_office && claim.status !== "COLLECTED" ? `. At ${claim.item.current_office.name}` : ""}
              .
            </p>
          </div>
          <Link href={`/claims/${claim.id}`} className={`button button-secondary ${styles.action}`}>
            Open
          </Link>
        </li>
      ))}
    </ul>
  );
}
