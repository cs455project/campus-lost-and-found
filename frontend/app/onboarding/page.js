"use client";

/**
 * First-time profile, shown once after the first sign-in.
 * Students give their name and roll number (locked afterwards, because
 * guards check them against the ID card). Staff only need a name.
 */

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import AuthLayout from "@/components/AuthLayout";
import { apiFetch } from "@/lib/api";

const EMPTY = { full_name: "", roll_number: "", phone: "", hall: "", room: "" };

export default function OnboardingPage() {
  const router = useRouter();
  const { user, loading, setUser } = useAuth();
  const [form, setForm] = useState(EMPTY);
  // A name that's already set (e.g. a former guard) is shown but locked.
  const nameLocked = Boolean(user?.full_name);

  useEffect(() => {
    if (user?.full_name) setForm((current) => ({ ...current, full_name: user.full_name }));
  }, [user?.full_name]);
  const [fieldErrors, setFieldErrors] = useState({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const isStaff = user?.role === "GUARD" || user?.role === "ADMIN";

  useEffect(() => {
    if (loading) return;
    if (!user) router.replace("/login");
    else if (user.is_onboarded) router.replace("/");
  }, [user, loading, router]);

  // One change handler for every input: the input's "name" says which field.
  function update(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
    setFieldErrors((current) => ({ ...current, [name]: undefined }));
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    setFieldErrors({});

    if (!form.full_name.trim()) {
      setFieldErrors({ full_name: "Enter your full name." });
      return;
    }
    if (!isStaff && !form.roll_number.trim()) {
      setFieldErrors({ roll_number: "Enter your roll number." });
      return;
    }
    if (!form.phone.trim()) {
      setFieldErrors({ phone: "Enter your mobile number." });
      return;
    }

    setBusy(true);
    try {
      const body = { ...form };
      if (isStaff) delete body.roll_number;
      const updated = await apiFetch("/users/me/onboarding", { method: "POST", body });
      setUser(updated);
      router.replace("/");
    } catch (err) {
      setFieldErrors(err.fieldErrors || {});
      setError(err.message);
      setBusy(false);
    }
  }

  if (loading || !user || user.is_onboarded) {
    return <p className="loading">Loading…</p>;
  }

  // Small helper so every field is built the same way, with its error linked
  // to the input for screen readers (aria-describedby).
  function field(name, label, { hint, ...inputProps } = {}) {
    const errorId = `${name}-error`;
    const hintId = `${name}-hint`;
    return (
      <div className="field">
        <label htmlFor={name}>{label}</label>
        {hint && (
          <span className="hint" id={hintId}>
            {hint}
          </span>
        )}
        <input
          id={name}
          name={name}
          value={form[name]}
          onChange={update}
          aria-invalid={fieldErrors[name] ? "true" : undefined}
          aria-describedby={[hint && hintId, fieldErrors[name] && errorId].filter(Boolean).join(" ") || undefined}
          {...inputProps}
        />
        {fieldErrors[name] && (
          <span className="field-error" id={errorId}>
            {fieldErrors[name]}
          </span>
        )}
      </div>
    );
  }

  return (
    <AuthLayout>
      <span className="eyebrow">One last step</span>
      <h1>Complete your profile</h1>
      <p className="lede">
        {isStaff
          ? "Your name is shown to students when you handle their items."
          : "Security guards check your name and roll number against your ID card when you collect an item, so they can't be changed later."}
      </p>

      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}

      <form onSubmit={submit} noValidate>
        <p className="req-note">
          Fields marked <span className="req">*</span> are required.
        </p>
        {field("full_name", <>Full name <span className="req">*</span></>, {
          hint: nameLocked
            ? "Already on your account. Ask the admin if it needs correcting."
            : isStaff
              ? undefined
              : "As printed on your student ID card.",
          autoComplete: "name",
          required: true,
          readOnly: nameLocked,
        })}
        {!isStaff &&
          field("roll_number", <>Roll number <span className="req">*</span></>, {
            autoComplete: "off",
            required: true,
          })}
        {field("phone", <>Mobile number <span className="req">*</span></>, {
          hint: "10-digit Indian mobile number.",
          type: "tel",
          required: true,
          autoComplete: "tel-national",
          inputMode: "tel",
        })}
        <div className="form-row">
          {field("hall", "Hall")}
          {field("room", "Room")}
        </div>

        <button type="submit" className="button button-full" disabled={busy}>
          {busy ? "Saving…" : "Save profile"}
        </button>
      </form>
    </AuthLayout>
  );
}
