/**
 * The split screen used before you're fully signed in (sign-in and the
 * first-time profile): a blue "pitch" panel on the left with a few sample
 * item pins, and the form on the right. Below 860px the blue panel is
 * hidden and only the form shows.
 */

import LogoMark from "@/components/Logo";
import styles from "./AuthLayout.module.css";

// Purely decorative examples on the pegboard (aria-hidden).
const PINS = [
  { kind: "FOUND", title: "Blue steel water bottle", place: "Sports complex" },
  { kind: "LOST", title: "Black backpack, laptop sleeve", place: "Central Library" },
  { kind: "RETURNED", title: "Casio fx-991 calculator", place: "Lecture Hall 7" },
];

export default function AuthLayout({ children }) {
  return (
    <div className={styles.split}>
      <section className={styles.pitch} aria-hidden="true">
        <div className={styles.brand}>
          <LogoMark size={40} className={styles.mark} />
          <span>
            <span className={styles.brandName}>TraceBack</span>
            <span className={styles.brandSub}>IIT Kanpur Lost &amp; Found</span>
          </span>
        </div>

        <div className={styles.pitchCopy}>
          <h2 className={styles.pitchTitle}>
            Every lost thing on campus, <em>one board.</em>
          </h2>
          <p>
            Report it, hand it in at a security office, and get it back with a quick ID check. No more asking around
            in hall groups.
          </p>
        </div>

        <ul className={styles.pins}>
          {PINS.map((pin) => (
            <li key={pin.title} className={styles.pin} data-kind={pin.kind}>
              <span className={styles.pinStamp}>{pin.kind}</span>
              <strong>{pin.title}</strong>
              <span>{pin.place}</span>
            </li>
          ))}
        </ul>
      </section>

      <main className={styles.formSide}>
        <div className={styles.formBox}>{children}</div>
      </main>
    </div>
  );
}
