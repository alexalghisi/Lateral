import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, Money, Spinner, StatusBadge } from "../components/ui";
import type { OrderStatus } from "../api/types";
import { STATUS_LABELS } from "../api/types";

const FILTERS: (OrderStatus | "all")[] = [
  "all",
  "pending",
  "accepted",
  "out_for_delivery",
  "delivered",
  "cancelled",
];

export function OrdersPage() {
  const { user } = useAuth();
  const [filter, setFilter] = useState<OrderStatus | "all">("all");

  const { data, isLoading, error } = useQuery({
    queryKey: ["orders", filter],
    queryFn: () => api.listOrders(filter === "all" ? undefined : filter),
    // A staff dashboard wants fresh data; poll while viewing.
    refetchInterval: 5000,
  });

  const isStaff = user?.role === "admin";

  return (
    <>
      <h1 className="page-title">{isStaff ? "All orders" : "My orders"}</h1>
      <p className="page-sub">
        {isStaff
          ? "Staff see every order. Open one to advance its status."
          : "You see only your own orders."}
      </p>

      <div className="tabs">
        {FILTERS.map((f) => (
          <button
            key={f}
            className={filter === f ? "active" : ""}
            onClick={() => setFilter(f)}
          >
            {f === "all" ? "All" : STATUS_LABELS[f]}
          </button>
        ))}
      </div>

      {isLoading && <Spinner />}
      {error && <ErrorNotice message={(error as Error).message} />}
      {data && data.length === 0 && (
        <div className="notice info">No orders to show here.</div>
      )}

      <div className="stack">
        {data?.map((order) => (
          <Link key={order.id} to={`/orders/${order.id}`} style={{ textDecoration: "none" }}>
            <div className="card clickable">
              <div className="row">
                <div>
                  <h3>Order #{order.id}</h3>
                  <p className="muted">
                    {order.items.length} item{order.items.length > 1 ? "s" : ""} ·{" "}
                    <Money cents={order.total_cents} />
                  </p>
                </div>
                <StatusBadge status={order.status} />
              </div>
            </div>
          </Link>
        ))}
      </div>
    </>
  );
}
