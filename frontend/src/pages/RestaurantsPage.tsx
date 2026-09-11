import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { ErrorNotice, Spinner } from "../components/ui";

export function RestaurantsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["restaurants"],
    queryFn: () => api.listRestaurants(),
  });

  return (
    <>
      <h1 className="page-title">Near you</h1>
      <p className="page-sub">Browsing is public — no account required.</p>

      {isLoading && <Spinner />}
      {error && <ErrorNotice message={(error as Error).message} />}

      {data && data.length === 0 && (
        <div className="notice info">
          No restaurants yet. An admin can add one from the Admin page.
        </div>
      )}

      <div className="grid">
        {data?.map((r) => (
          <Link key={r.id} to={`/restaurants/${r.id}`} style={{ textDecoration: "none" }}>
            <div className="card clickable">
              <div className="row">
                <h3>{r.name}</h3>
                {!r.is_active && <span className="badge cancelled">Inactive</span>}
              </div>
              <p className="muted">{r.description ?? "—"}</p>
              <span className="faint">View menu →</span>
            </div>
          </Link>
        ))}
      </div>
    </>
  );
}
