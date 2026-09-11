import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import {
  ErrorNotice,
  Money,
  OrderTracker,
  Spinner,
  StatusBadge,
} from "../components/ui";
import { ALLOWED_TRANSITIONS, STATUS_LABELS } from "../api/types";
import type { OrderStatus } from "../api/types";

export function OrderDetailPage() {
  const { id } = useParams();
  const orderId = Number(id);
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const { data: order, isLoading, error } = useQuery({
    queryKey: ["order", orderId],
    queryFn: () => api.getOrder(orderId),
    // Live tracking: poll until the order reaches a terminal state.
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "delivered" || s === "cancelled" ? false : 4000;
    },
  });

  const advance = useMutation({
    mutationFn: (status: OrderStatus) => api.updateOrderStatus(orderId, status),
    onSuccess: (updated) => {
      queryClient.setQueryData(["order", orderId], updated);
      void queryClient.invalidateQueries({ queryKey: ["orders"] });
    },
  });

  if (isLoading) return <Spinner />;
  if (error) return <ErrorNotice message={(error as Error).message} />;
  if (!order) return <ErrorNotice message="Order not found." />;

  const isStaff = user?.role === "admin";
  const nextStates = ALLOWED_TRANSITIONS[order.status];

  return (
    <>
      <p>
        <Link to="/orders">← Orders</Link>
      </p>
      <div className="row">
        <h1 className="page-title">Order #{order.id}</h1>
        <StatusBadge status={order.status} />
      </div>
      <p className="page-sub">
        Placed {new Date(order.created_at).toLocaleString()}
      </p>

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
        <div className="receipt">
          {order.items.map((line) => (
            <div key={line.id} className="line">
              <span>
                {line.quantity} × {line.item_name}
              </span>
              <Money cents={line.subtotal_cents} />
            </div>
          ))}
          <div className="line total">
            <span>Total</span>
            <Money cents={order.total_cents} />
          </div>
          <p className="faint" style={{ marginTop: 10, marginBottom: 0 }}>
            Item names and prices are snapshotted onto the order — a receipt, not a
            live join.
          </p>
        </div>

        <div className="card">
          <h3 style={{ marginBottom: 8 }}>Progress</h3>
          <OrderTracker status={order.status} />

          {isStaff && (
            <>
              <hr style={{ border: "none", borderTop: "1px solid var(--line)" }} />
              <p className="muted" style={{ marginTop: 12 }}>
                Advance the order (staff only):
              </p>
              {advance.error && (
                <ErrorNotice
                  message={
                    advance.error instanceof ApiError
                      ? advance.error.message
                      : "Could not update the order."
                  }
                />
              )}
              <div className="stack">
                {nextStates.length === 0 ? (
                  <span className="faint">Terminal state — nothing follows.</span>
                ) : (
                  nextStates.map((status) => (
                    <button
                      key={status}
                      className={status === "cancelled" ? "btn subtle" : "btn"}
                      onClick={() => advance.mutate(status)}
                      disabled={advance.isPending}
                    >
                      Mark {STATUS_LABELS[status]}
                    </button>
                  ))
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
