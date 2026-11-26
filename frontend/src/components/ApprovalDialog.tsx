import { useEffect, useRef, useState } from "react";
import type { Proposal } from "../api/client";

export function ApprovalDialog({
  proposal,
  busy,
  onDecision,
  onExpire,
}: {
  proposal: Proposal;
  busy: boolean;
  onDecision: (decision: "approve" | "decline" | "cancel") => void;
  onExpire: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const decline = useRef<HTMLButtonElement>(null);
  const [seconds, setSeconds] = useState(() =>
    Math.max(
      0,
      Math.ceil((Date.parse(proposal.expires_at) - Date.now()) / 1000),
    ),
  );
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    decline.current?.focus();
    return () => element?.close();
  }, []);
  useEffect(() => {
    const tick = () => {
      const remaining = Math.max(
        0,
        Math.ceil((Date.parse(proposal.expires_at) - Date.now()) / 1000),
      );
      setSeconds(remaining);
      if (remaining === 0 && !busy) onExpire();
    };
    tick();
    const timer = window.setInterval(tick, 500);
    return () => window.clearInterval(timer);
  }, [proposal.expires_at, busy, onExpire]);
  return (
    <dialog
      ref={dialog}
      aria-labelledby="approval-title"
      aria-describedby="approval-description"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onDecision("cancel");
      }}
    >
      <div className="dialog-heading">
        <span className="eyebrow">Your permission</span>
        <span className="pill">{seconds}s remaining</span>
      </div>
      <h2 id="approval-title">Allow this cloud request?</h2>
      <p id="approval-description">
        Review the exact sanitized text below. Approving authorizes one request
        to <strong>{proposal.model_id}</strong>.
      </p>
      <p className="subtle">
        Reason: {proposal.reason.replaceAll("_", " ")} · Expires{" "}
        {new Date(proposal.expires_at).toLocaleTimeString()}
      </p>
      <h3>Sanitized preview</h3>
      <pre className="preview" aria-label="Exact sanitized preview">
        {proposal.sanitized_preview}
      </pre>
      {proposal.best_local_answer && (
        <>
          <h3>Best local answer</h3>
          <pre className="preview">{proposal.best_local_answer}</pre>
        </>
      )}
      <p className="fine-print">
        The broker keeps restoration details on this device. The mock demo uses
        a synthetic cloud provider; real local mode has cloud disabled.
      </p>
      <div className="dialog-actions">
        <button
          ref={decline}
          disabled={busy}
          onClick={() => onDecision("decline")}
        >
          Keep local
        </button>
        <button
          className="primary"
          disabled={busy || seconds === 0}
          onClick={() => onDecision("approve")}
        >
          {busy ? "Finishing…" : "Approve once"}
        </button>
      </div>
    </dialog>
  );
}
