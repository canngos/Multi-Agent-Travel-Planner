import { useEffect, useRef, useState, type FormEvent } from "react";

import { renderMarkdown } from "../lib/format";
import { humanize } from "../lib/graph";
import type { ChatMessage } from "../lib/run";

/** What the "Approve" button sends: the concierge decides what it means (approve_plan). */
export const APPROVE_TEXT = "Looks good, please write the guide.";

// The recorded demo's messages, in order (travel_planner/demo_script.py; tests/test_demo_replay.py keeps them equal)
export const DEMO_LINES = [
  "Plan 5 days in Lisbon for two, mid budget, we love history and street food.",
  "We fly from Helsinki, 10-14 May. Total budget about 2500 euros.",
  "Yes, that's right, go ahead.",
  APPROVE_TEXT,
  "Thanks! Which day do we visit Belém, and what does the tower cost?",
  "Could we take it a bit slower, with a relaxed pace, and stay somewhere cheaper?",
  "Yes, please do that.",
  APPROVE_TEXT,
];

interface Props {
  messages: ChatMessage[];
  awaitingApproval?: boolean;
  mode: "live" | "replay";
  canSend: boolean;
  busy: boolean;
  sent: string[];
  onSend: (text: string) => void;
  onOpenGuide: () => void;
}

export function Chat({ messages, awaitingApproval, mode, canSend, busy, sent, onSend, onOpenGuide }: Props) {
  const [draft, setDraft] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const list = useRef<HTMLOListElement>(null);
  useEffect(() => {  // scroll the message list only (scrollIntoView would also scroll the page)
    const el = list.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages.length, busy]);

  // the next demo line, as long as the conversation so far followed the demo exactly
  const nextDemo = sent.every((text, i) => text === DEMO_LINES[i]) ? DEMO_LINES[sent.length] : undefined;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || !canSend) return;
    onSend(text);
    setDraft("");
  };

  return (
    <section className="chat" aria-label="Chat with the concierge">
      <header className="panel-head">
        <h2>Concierge</h2>
        <p>{mode === "live" ? "Tell it where and when you want to travel." : "Replaying a recorded conversation."}</p>
      </header>

      <ol className="messages" ref={list} aria-live="polite">
        {messages.length === 0 && (
          <li className="message empty">
            <p>Where would you like to go? Say the city, the dates, who is travelling and the budget, and the concierge
              asks for anything missing. It confirms the details with you before the planning team starts.</p>
          </li>
        )}
        {messages.map((m) => (
          <li key={m.id} className={`message from-${m.role}`}>
            <span className="who">{m.role === "user" ? "You" : humanize(m.author)}</span>
            {m.role === "user" || m.role === "notice"
              ? <p>{m.text}</p>
              : <div className="md" dangerouslySetInnerHTML={{ __html: renderMarkdown(m.text) }} />}
            {m.role === "notice" && m.text.startsWith("The trip guide is ready") && (
              <button type="button" className="link-button" onClick={onOpenGuide}>Open the guide</button>
            )}
          </li>
        ))}
        {busy && <li className="message typing" aria-label="The planner is working"><span /><span /><span /></li>}
      </ol>

      {mode === "live" && (
        <form className="composer" onSubmit={submit}>
          {awaitingApproval && !busy && (
            <div className="approval" role="group" aria-label="Approve the reviewed plan">
              <p>The plan has been reviewed. Approve it to get the full guide, or tell the concierge what to change.</p>
              <div className="approval-actions">
                <button type="button" className="primary" onClick={() => onSend(APPROVE_TEXT)} disabled={!canSend}>
                  Approve and write the guide
                </button>
                <button type="button" onClick={() => input.current?.focus()} disabled={!canSend}>Request changes</button>
              </div>
            </div>
          )}
          {nextDemo && !busy && (
            <button type="button" className="suggestion" onClick={() => onSend(nextDemo)} disabled={!canSend}>
              <span>Send demo message {sent.length + 1} of {DEMO_LINES.length}</span>
              {nextDemo}
            </button>
          )}
          <label htmlFor="chat-input" className="visually-hidden">Message to the concierge</label>
          <textarea id="chat-input" ref={input} rows={2} value={draft} placeholder={busy ? "The planner is working…" : canSend ? "Write a message…" : "The planner server is not connected"}
                    onChange={(e) => setDraft(e.target.value)} disabled={!canSend}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) submit(e); }} />
          <button type="submit" className="send" disabled={!canSend || !draft.trim()}>Send</button>
        </form>
      )}
    </section>
  );
}
