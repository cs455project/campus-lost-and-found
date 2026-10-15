"use client";

/**
 * Staff-only section of an item's page: who reported it, the actions allowed
 * right now (receive, move to another office) and the custody history.
 *
 * Which actions appear comes from the backend (can_receive, can_transfer);
 * the backend checks them again when an action is sent.
 */

import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { apiFetch } from "@/lib/api";
import { EVENT_LABELS, formatDateTime } from "@/lib/labels";
import styles from "./StaffPanel.module.css";

export default function StaffPanel({ item, onChanged }) {
  const [offices, setOffices] = useState([]);

  useEffect(() => {
    apiFetch("/offices").then(setOffices).catch(() => setOffices([]));
  }, []);

  return (
    <section className={styles.panel} aria-labelledby="staff-heading">
      <h2 id="staff-heading">Staff</h2>

      {item.reporter && (
        <p className={styles.reporter}>
          {item.custody_events[0]?.event_type === "REGISTERED" ? "Registered by " : "Reported by "}
          <strong>{item.reporter.full_name}</strong>
          {item.reporter.roll_number ? `, roll number ${item.reporter.roll_number}` : ""} ({item.reporter.email})
        </p>
      )}

      <OwnerAlerts alerts={item.owner_alerts || []} />

      {item.can_receive && <ReceiveForm item={item} offices={offices} onDone={onChanged} />}
      {item.can_transfer && <TransferForm item={item} offices={offices} onDone={onChanged} />}

      <History events={item.custody_events} />
    </section>
  );
}

/** Phase D: owners identified from the text on the photos (masked). */
function OwnerAlerts({ alerts }) {
  if (!alerts.length) return null;
  return (
    <div className={styles.ownerAlerts}>
      <h3>Owner found on the item</h3>
      <ul>
        {alerts.map((alert, index) => (
          <li key={index}>
            {alert.id_type === "ROLL" ? "Roll number" : "Email"} ({alert.id_hint})
            {alert.registered ? ", has an account" : ""}:{" "}
            {alert.status === "sent"
              ? `emailed ${formatDateTime(alert.sent_at)}`
              : "will be emailed when the item is received at an office"}
          </li>
        ))}
      </ul>
      <p className={styles.small}>Read automatically from the photo on our server. Check the ID before handing over.</p>
    </div>
  );
}

/** Shared bits for the action forms. */
function useAction(path, onDone) {
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState({});
  const [busy, setBusy] = useState(false);

  async function send(body) {
    setBusy(true);
    setError("");
    setFieldErrors({});
    try {
      onDone(await apiFetch(path, { method: "POST", body }));
      return true;
    } catch (err) {
      setError(err.message);
      setFieldErrors(err.fieldErrors || {});
      return false;
    } finally {
      setBusy(false);
    }
  }
  return { send, error, fieldErrors, busy };
}

function ErrorBox({ message }) {
  return message ? (
    <div className="alert" role="alert">
      {message}
    </div>
  ) : null;
}

function ReceiveForm({ item, offices, onDone }) {
  const { user } = useAuth();
  // Default: the office the finder chose, else the guard's usual office.
  const [officeId, setOfficeId] = useState(String(item.dropoff_office?.id || user.office?.id || ""));
  const [condition, setCondition] = useState("");
  const { send, error, fieldErrors, busy } = useAction(`/staff/items/${item.id}/receive`, onDone);

  return (
    <form
      className={styles.action}
      onSubmit={(event) => {
        event.preventDefault();
        send({ office_id: Number(officeId), condition_note: condition });
      }}
    >
      <h3>Receive this item</h3>
      <p className={styles.small}>
        Check the item matches the report above. Once received, it appears on the Found page.
      </p>
      <ErrorBox message={error} />
      <div className="field">
        <label htmlFor="receive-office">Received at <span className="req">*</span></label>
        <select id="receive-office" value={officeId} onChange={(e) => setOfficeId(e.target.value)}>
          {offices.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="receive-condition">Condition</label>
        <input
          id="receive-condition"
          value={condition}
          onChange={(e) => setCondition(e.target.value)}
          maxLength={300}
          placeholder="e.g. Screen cracked, zip torn"
        />
      </div>
      <button type="submit" className="button" disabled={busy || !officeId}>
        {busy ? "Receiving…" : "Receive item"}
      </button>
    </form>
  );
}

function TransferForm({ item, offices, onDone }) {
  const choices = offices.filter((o) => o.id !== item.current_office?.id);
  const [officeId, setOfficeId] = useState("");
  const [note, setNote] = useState("");
  const { send, error, busy } = useAction(`/staff/items/${item.id}/transfer`, onDone);

  return (
    <form
      className={styles.action}
      onSubmit={async (event) => {
        event.preventDefault();
        if (await send({ to_office_id: Number(officeId), note })) {
          setOfficeId("");
          setNote("");
        }
      }}
    >
      <h3>Move to another office</h3>
      <ErrorBox message={error} />
      <div className="form-row">
        <div className="field">
          <label htmlFor="transfer-office">Move to <span className="req">*</span></label>
          <select id="transfer-office" value={officeId} onChange={(e) => setOfficeId(e.target.value)}>
            <option value="">Choose an office</option>
            {choices.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="transfer-note">Note</label>
          <input id="transfer-note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
        </div>
      </div>
      <button type="submit" className="button button-secondary" disabled={busy || !officeId}>
        {busy ? "Moving…" : "Move item"}
      </button>
    </form>
  );
}

function History({ events }) {
  if (!events.length) {
    return <p className={styles.small}>No custody history yet: the item hasn&apos;t been handed in.</p>;
  }
  return (
    <>
      <h3>Custody history</h3>
      {/* An ordered list: the history really is a sequence, oldest first. */}
      <ol className={styles.history}>
        {events.map((event) => (
          <li key={event.id}>
            <strong>{EVENT_LABELS[event.event_type]}</strong>
            <span className={styles.small}>
              {event.from_office && ` from ${event.from_office.name}`}
              {event.to_office && ` ${event.from_office ? "to" : "at"} ${event.to_office.name}`}
              {event.actor_name && `, by ${event.actor_name}`}
            </span>
            <span className={styles.when}>{formatDateTime(event.created_at)}</span>
            {event.note && <span className={styles.note}>{event.note}</span>}
          </li>
        ))}
      </ol>
    </>
  );
}
