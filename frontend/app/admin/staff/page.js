"use client";

/**
 * Manage staff (admins only): add, change and remove guards and admins.
 *
 *  - Guards can receive, move and hand over items at every office.
 *  - Admins can do all that, and manage staff (this page).
 *  - "Usual office" only records where someone normally sits (it's the
 *    default office on their forms); everyone can work at every office.
 *
 * You can't change your own role or remove yourself (the backend refuses
 * too), so there is always at least one admin.
 */

import { useCallback, useEffect, useState } from "react";

import AppShell from "@/components/AppShell";
import { useAuth } from "@/components/AuthProvider";
import { apiFetch } from "@/lib/api";
import styles from "./staff.module.css";

const ROLE_NAMES = { GUARD: "Guard", ADMIN: "Admin" };

export default function ManageStaffPage() {
  return (
    <AppShell require="admin" wide>
      <ManageStaff />
    </AppShell>
  );
}

function ManageStaff() {
  const { user } = useAuth();
  const [staff, setStaff] = useState(null);
  const [offices, setOffices] = useState([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(() => {
    apiFetch("/admin/staff").then(setStaff).catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    load();
    apiFetch("/offices").then(setOffices).catch(() => setOffices([]));
  }, [load]);

  function done(text) {
    setMessage(text);
    setError("");
    load();
  }

  const admins = staff?.filter((m) => m.role === "ADMIN") || [];
  const guards = staff?.filter((m) => m.role === "GUARD") || [];

  return (
    <>
      <span className="eyebrow">Admin</span>
      <h1>Manage staff</h1>
      <p className="lede">
        Guards receive, move and hand over items at every office. Admins can also add, change and remove staff.
      </p>

      {message && (
        <div className="success" role="status">
          {message}
        </div>
      )}
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}

      <AddStaff
        offices={offices}
        onAdded={(m) => done(`${m.email} is now ${m.role === "ADMIN" ? "an admin" : "a guard"}.`)}
        onError={setError}
      />

      {!staff && !error && <p className="loading">Loading…</p>}
      {staff && (
        <>
          <StaffList title="Admins" members={admins} me={user} offices={offices} onChanged={done} onError={setError} />
          <StaffList
            title="Guards"
            members={guards}
            me={user}
            offices={offices}
            onChanged={done}
            onError={setError}
            empty="No guards yet. Add one above."
          />
        </>
      )}
    </>
  );
}

function AddStaff({ offices, onAdded, onError }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("GUARD");
  const [officeId, setOfficeId] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    try {
      const member = await apiFetch("/admin/staff", {
        method: "POST",
        body: { email: email.trim(), role, office_id: officeId ? Number(officeId) : null },
      });
      setEmail("");
      setOfficeId("");
      setRole("GUARD");
      onAdded(member);
    } catch (err) {
      onError(err.fieldErrors?.email ? "Enter a valid email address." : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className={styles.add}>
      <h2>Add staff</h2>
      <p className={styles.hint}>
        Their role starts from their next sign-in. If they&apos;ve never signed in, their account is created now.
      </p>
      <div className={styles.addFields}>
        <div className="field">
          <label htmlFor="staff-email">Email (@iitk.ac.in or @gmail.com) <span className="req">*</span></label>
          <input
            id="staff-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="name@gmail.com"
          />
        </div>
        <div className="field">
          <label htmlFor="staff-role">Role <span className="req">*</span></label>
          <select id="staff-role" value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="GUARD">Guard</option>
            <option value="ADMIN">Admin</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="staff-office">Usual office</label>
          <select id="staff-office" value={officeId} onChange={(e) => setOfficeId(e.target.value)}>
            <option value="">None</option>
            {offices.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="button" disabled={busy || !email.trim()}>
          {busy ? "Adding…" : role === "ADMIN" ? "Add admin" : "Add guard"}
        </button>
      </div>
      {role === "ADMIN" && (
        <p className={styles.warning}>Admins can add and remove other staff, including admins. Only add people you trust.</p>
      )}
    </form>
  );
}

function StaffList({ title, members, me, offices, onChanged, onError, empty }) {
  return (
    <section className={styles.section} aria-labelledby={`list-${title}`}>
      <h2 id={`list-${title}`} className={styles.listHeading}>
        {title} <span className={styles.count}>{members.length}</span>
      </h2>
      {members.length === 0 ? (
        <p className={styles.empty}>{empty}</p>
      ) : (
        <ul className={styles.list}>
          {members.map((member) => (
            <StaffRow
              key={member.id}
              member={member}
              isMe={member.id === me.id}
              offices={offices}
              onChanged={onChanged}
              onError={onError}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function StaffRow({ member, isMe, offices, onChanged, onError }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const name = member.full_name || member.email;

  async function update(changes, text) {
    try {
      await apiFetch(`/admin/staff/${member.id}`, { method: "PATCH", body: changes });
      onChanged(text);
    } catch (err) {
      onError(err.message);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await apiFetch(`/admin/staff/${member.id}`, { method: "DELETE" });
      onChanged(`${name} is no longer staff.`);
    } catch (err) {
      onError(err.message);
      setBusy(false);
    }
  }

  return (
    <li className={styles.row}>
      <div className={styles.who}>
        <strong>
          {member.full_name || "Not signed in yet"}
          {isMe && <span className="pill pill-brand"> You</span>}
        </strong>
        <span className={styles.email}>{member.email}</span>
      </div>

      <div className="field">
        <label htmlFor={`role-${member.id}`} className="visually-hidden">
          Role for {name}
        </label>
        <select
          id={`role-${member.id}`}
          value={member.role}
          disabled={isMe}
          title={isMe ? "You can't change your own role" : undefined}
          onChange={(e) =>
            update({ role: e.target.value }, `${name} is now ${e.target.value === "ADMIN" ? "an admin" : "a guard"}.`)
          }
        >
          <option value="GUARD">{ROLE_NAMES.GUARD}</option>
          <option value="ADMIN">{ROLE_NAMES.ADMIN}</option>
        </select>
      </div>

      <div className="field">
        <label htmlFor={`office-${member.id}`} className="visually-hidden">
          Usual office for {name}
        </label>
        <select
          id={`office-${member.id}`}
          value={member.office?.id || ""}
          onChange={(e) =>
            update({ office_id: e.target.value ? Number(e.target.value) : null }, `Updated ${name}'s usual office.`)
          }
        >
          <option value="">No usual office</option>
          {offices.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
      </div>

      {isMe ? (
        <span className={styles.selfNote}>Another admin can change your role</span>
      ) : confirming ? (
        <div className={styles.confirm}>
          <span>Remove {name}? They lose staff access immediately.</span>
          <div className="button-row">
            <button type="button" className="button button-danger" onClick={remove} disabled={busy}>
              {busy ? "Removing…" : "Remove"}
            </button>
            <button type="button" className="link-button" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="button button-secondary" onClick={() => setConfirming(true)}>
          Remove
        </button>
      )}
    </li>
  );
}
