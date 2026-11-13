"use client";

/**
 * The help assistant. In the sidebar it is a small card ("Ask TraceBack");
 * on phones a round button in the corner. Either opens a chat panel.
 *
 * The conversation lives only in this browser tab: each question is sent
 * with the recent messages to POST /api/assistant/chat, which answers with
 * OpenAI, Gemini or Groq (whichever is up first) and can only *read*:
 * found items held at an office, your own reports and claims, and the
 * offices. Item and claim references in answers become links.
 *
 * The panel is rendered into <body> (a portal): the sidebar is
 * position: sticky, which makes its own stacking layer, so a panel inside
 * it would slide under the item cards whatever its z-index.
 */

import { MessageCircle, Send, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { apiFetch } from "@/lib/api";
import styles from "./AssistantChat.module.css";

const SUGGESTIONS = [
  "Has anyone found a black wallet?",
  "What's the status of my claims?",
  "Where is the security office?",
  "How do I claim a found item?",
];

const REF = /\b(FND|LST|CLM)-(\d{4,})\b/g;

/** Plain text with FND-0012 / LST-0003 / CLM-0007 turned into links. */
function withLinks(text) {
  const parts = [];
  let last = 0;
  for (const match of text.matchAll(REF)) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const id = Number(match[2]);
    const href = match[1] === "CLM" ? `/claims/${id}` : `/items/${id}`;
    parts.push(
      <Link key={`${match.index}-${match[0]}`} href={href} className={styles.ref}>
        {match[0]}
      </Link>,
    );
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export default function AssistantChat() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [mounted, setMounted] = useState(false);
  const listRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  async function ask(text) {
    const question = text.trim();
    if (!question || busy) return;
    const next = [...messages, { role: "user", content: question }];
    setMessages(next);
    setDraft("");
    setError("");
    setBusy(true);
    try {
      const data = await apiFetch("/assistant/chat", { method: "POST", body: { messages: next.slice(-12) } });
      setMessages([...next, { role: "assistant", content: data.reply }]);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      ask(draft);
    }
  }

  return (
    <>
      <button type="button" className={styles.card} onClick={() => setOpen(true)} aria-haspopup="dialog">
        <Sparkles size={16} strokeWidth={1.8} aria-hidden="true" />
        <span>
          <strong>Ask TraceBack</strong>
          <span className={styles.cardText}>Questions about lost items, claims or offices? Ask the assistant.</span>
        </span>
      </button>

      <button type="button" className={styles.fab} onClick={() => setOpen(true)} aria-label="Ask the TraceBack assistant">
        <MessageCircle size={22} aria-hidden="true" />
      </button>

      {open && mounted && createPortal(
        <section className={styles.panel} role="dialog" aria-label="TraceBack assistant">
          <header className={styles.head}>
            <Sparkles size={16} aria-hidden="true" />
            <div>
              <strong>TraceBack assistant</strong>
              <span>AI can make mistakes. Guards decide every claim.</span>
            </div>
            <button type="button" className={styles.close} onClick={() => setOpen(false)} aria-label="Close">
              <X size={18} />
            </button>
          </header>

          <div className={styles.messages} ref={listRef} aria-live="polite">
            {messages.length === 0 && (
              <div className={styles.empty}>
                <p>Hi! I can search found items, check your reports and claims, and tell you where the offices are.</p>
                <div className={styles.suggestions}>
                  {SUGGESTIONS.map((text) => (
                    <button key={text} type="button" onClick={() => ask(text)}>
                      {text}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((message, index) => (
              <p key={index} className={message.role === "user" ? styles.user : styles.bot}>
                {message.role === "assistant" ? withLinks(message.content) : message.content}
              </p>
            ))}
            {busy && <p className={`${styles.bot} ${styles.thinking}`}>Looking that up…</p>}
            {error && <p className={styles.error}>{error}</p>}
          </div>

          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault();
              ask(draft);
            }}
          >
            <textarea
              ref={inputRef}
              rows={1}
              maxLength={1000}
              value={draft}
              placeholder="Ask about an item, a claim, an office…"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={onKeyDown}
              aria-label="Your question"
            />
            <button type="submit" disabled={busy || !draft.trim()} aria-label="Send">
              <Send size={17} />
            </button>
          </form>
        </section>,
        document.body,
      )}
    </>
  );
}
