// Small presentational helpers shared across pages.

import type { OrderStatus } from "../api/types";
import { ORDER_FLOW, STATUS_LABELS } from "../api/types";

/** Format integer cents as euros, e.g. 2100 -> "€21.00". */
export function euros(cents: number): string {
  return `€${(cents / 100).toFixed(2)}`;
}

export function Money({ cents }: { cents: number }) {
  return <span>{euros(cents)}</span>;
}

export function StatusBadge({ status }: { status: OrderStatus }) {
  return <span className={`badge ${status}`}>{STATUS_LABELS[status]}</span>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return <div className="spinner">{label}</div>;
}

export function ErrorNotice({ message }: { message: string }) {
  return <div className="notice error">{message}</div>;
}

/** Vertical lifecycle tracker. `cancelled` is shown as a terminal state. */
export function OrderTracker({ status }: { status: OrderStatus }) {
  if (status === "cancelled") {
    return (
      <div className="tracker">
        <div className="step current">
          <span className="dot" />
          <span className="label">Cancelled</span>
        </div>
      </div>
    );
  }

  const currentIndex = ORDER_FLOW.indexOf(status);
  return (
    <div className="tracker">
      {ORDER_FLOW.map((step, i) => {
        const state = i < currentIndex ? "done" : i === currentIndex ? "current" : "";
        return (
          <div key={step}>
            <div className={`step ${state}`}>
              <span className="dot" />
              <span className="label">{STATUS_LABELS[step]}</span>
            </div>
            {i < ORDER_FLOW.length - 1 && (
              <div className={`connector ${i < currentIndex ? "done" : ""}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}
