import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import { ErrorNotice, Money, Spinner } from "../components/ui";

export function AdminPage() {
  const queryClient = useQueryClient();
  const restaurants = useQuery({
    queryKey: ["restaurants"],
    queryFn: () => api.listRestaurants(),
  });

  // Create-restaurant form
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const createRestaurant = useMutation({
    mutationFn: () => api.createRestaurant(name, description || null),
    onSuccess: () => {
      setName("");
      setDescription("");
      void queryClient.invalidateQueries({ queryKey: ["restaurants"] });
    },
  });

  // Add-menu-item form
  const [restaurantId, setRestaurantId] = useState<number | "">("");
  const [itemName, setItemName] = useState("");
  const [itemDesc, setItemDesc] = useState("");
  const [price, setPrice] = useState("");

  const addItem = useMutation({
    mutationFn: () =>
      api.addMenuItem(Number(restaurantId), {
        name: itemName,
        description: itemDesc || null,
        // The UI collects euros; the API stores integer cents.
        price_cents: Math.round(parseFloat(price) * 100),
      }),
    onSuccess: () => {
      setItemName("");
      setItemDesc("");
      setPrice("");
      void queryClient.invalidateQueries({ queryKey: ["menu", Number(restaurantId)] });
    },
  });

  function onCreateRestaurant(e: FormEvent) {
    e.preventDefault();
    createRestaurant.mutate();
  }
  function onAddItem(e: FormEvent) {
    e.preventDefault();
    if (restaurantId === "") return;
    addItem.mutate();
  }

  return (
    <>
      <h1 className="page-title">Admin</h1>
      <p className="page-sub">
        Catalogue management. These endpoints require the admin role — the same
        token that renders this page authorises the writes.
      </p>

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", alignItems: "start" }}>
        <form className="card" onSubmit={onCreateRestaurant}>
          <h3 style={{ marginBottom: 12 }}>New restaurant</h3>
          {createRestaurant.error && (
            <ErrorNotice
              message={
                createRestaurant.error instanceof ApiError
                  ? createRestaurant.error.message
                  : "Could not create the restaurant."
              }
            />
          )}
          <div className="field">
            <label>Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="field">
            <label>Description</label>
            <input
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <button className="btn" disabled={createRestaurant.isPending}>
            {createRestaurant.isPending ? "Creating…" : "Create restaurant"}
          </button>
        </form>

        <form className="card" onSubmit={onAddItem}>
          <h3 style={{ marginBottom: 12 }}>Add menu item</h3>
          {addItem.error && (
            <ErrorNotice
              message={
                addItem.error instanceof ApiError
                  ? addItem.error.message
                  : "Could not add the item."
              }
            />
          )}
          <div className="field">
            <label>Restaurant</label>
            <select
              value={restaurantId}
              onChange={(e) =>
                setRestaurantId(e.target.value === "" ? "" : Number(e.target.value))
              }
              required
            >
              <option value="">Select…</option>
              {restaurants.data?.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Item name</label>
            <input value={itemName} onChange={(e) => setItemName(e.target.value)} required />
          </div>
          <div className="field">
            <label>Description</label>
            <input value={itemDesc} onChange={(e) => setItemDesc(e.target.value)} />
          </div>
          <div className="field">
            <label>Price (€)</label>
            <input
              type="number"
              min="0"
              step="0.01"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              required
            />
          </div>
          <button className="btn" disabled={addItem.isPending}>
            {addItem.isPending ? "Adding…" : "Add item"}
          </button>
        </form>
      </div>

      <h2 style={{ margin: "32px 0 16px" }}>Restaurants</h2>
      {restaurants.isLoading && <Spinner />}
      <div className="grid">
        {restaurants.data?.map((r) => (
          <MenuPreview key={r.id} restaurantId={r.id} name={r.name} />
        ))}
      </div>
    </>
  );
}

function MenuPreview({ restaurantId, name }: { restaurantId: number; name: string }) {
  const { data } = useQuery({
    queryKey: ["menu", restaurantId],
    queryFn: () => api.listMenu(restaurantId),
  });
  return (
    <div className="card">
      <h3>{name}</h3>
      {data?.length ? (
        data.map((item) => (
          <div key={item.id} className="row" style={{ padding: "4px 0" }}>
            <span className="muted">{item.name}</span>
            <Money cents={item.price_cents} />
          </div>
        ))
      ) : (
        <p className="faint">No items yet.</p>
      )}
    </div>
  );
}
