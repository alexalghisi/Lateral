import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, Money, Spinner } from "../components/ui";

export function MenuPage() {
  const { id } = useParams();
  const restaurantId = Number(id);
  const navigate = useNavigate();
  const { user } = useAuth();

  // menuItemId -> quantity
  const [basket, setBasket] = useState<Record<number, number>>({});

  const restaurant = useQuery({
    queryKey: ["restaurant", restaurantId],
    queryFn: () => api.getRestaurant(restaurantId),
  });
  const menu = useQuery({
    queryKey: ["menu", restaurantId],
    queryFn: () => api.listMenu(restaurantId),
  });

  const total = useMemo(() => {
    if (!menu.data) return 0;
    return menu.data.reduce(
      (sum, item) => sum + (basket[item.id] ?? 0) * item.price_cents,
      0,
    );
  }, [basket, menu.data]);

  const lineCount = Object.values(basket).reduce((a, b) => a + b, 0);

  const placeOrder = useMutation({
    mutationFn: () => {
      const items = Object.entries(basket)
        .filter(([, qty]) => qty > 0)
        .map(([menuItemId, qty]) => ({ menu_item_id: Number(menuItemId), quantity: qty }));
      return api.placeOrder(restaurantId, items);
    },
    onSuccess: (order) => navigate(`/orders/${order.id}`),
  });

  function setQty(itemId: number, delta: number) {
    setBasket((prev) => {
      const next = Math.max(0, (prev[itemId] ?? 0) + delta);
      return { ...prev, [itemId]: next };
    });
  }

  function onPlaceOrder() {
    if (!user) {
      navigate("/login", { state: { from: `/restaurants/${restaurantId}` } });
      return;
    }
    placeOrder.mutate();
  }

  if (restaurant.isLoading || menu.isLoading) return <Spinner />;
  if (restaurant.error)
    return <ErrorNotice message={(restaurant.error as Error).message} />;

  return (
    <>
      <p>
        <Link to="/">← Restaurants</Link>
      </p>
      <h1 className="page-title">{restaurant.data?.name}</h1>
      <p className="page-sub">{restaurant.data?.description ?? "—"}</p>

      {menu.error && <ErrorNotice message={(menu.error as Error).message} />}
      {placeOrder.error && (
        <ErrorNotice
          message={
            placeOrder.error instanceof ApiError
              ? placeOrder.error.message
              : "Could not place the order."
          }
        />
      )}

      <div className="stack">
        {menu.data?.length === 0 && (
          <div className="notice info">This restaurant has no available items yet.</div>
        )}
        {menu.data?.map((item) => (
          <div key={item.id} className="card">
            <div className="row">
              <div>
                <h3>{item.name}</h3>
                <p className="muted">{item.description ?? "—"}</p>
                <strong>
                  <Money cents={item.price_cents} />
                </strong>
              </div>
              <div className="stepper">
                <button onClick={() => setQty(item.id, -1)} aria-label="Remove one">
                  −
                </button>
                <span className="qty">{basket[item.id] ?? 0}</span>
                <button onClick={() => setQty(item.id, 1)} aria-label="Add one">
                  +
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {lineCount > 0 && (
        <div className="card" style={{ marginTop: 20, position: "sticky", bottom: 16 }}>
          <div className="row">
            <span className="muted">
              {lineCount} item{lineCount > 1 ? "s" : ""} · <Money cents={total} />
            </span>
            <button className="btn" onClick={onPlaceOrder} disabled={placeOrder.isPending}>
              {placeOrder.isPending
                ? "Placing order…"
                : user
                  ? "Place order"
                  : "Sign in to order"}
            </button>
          </div>
          <p className="faint" style={{ marginTop: 8, marginBottom: 0 }}>
            The request carries no price — the server computes the total from the menu.
          </p>
        </div>
      )}
    </>
  );
}
