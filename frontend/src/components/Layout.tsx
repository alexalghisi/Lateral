import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function Layout() {
  const { user, logout } = useAuth();

  return (
    <>
      <nav className="nav">
        <div className="nav-inner">
          <NavLink to="/" className="brand">
            Lateral<span>.</span>
          </NavLink>
          <NavLink to="/" end>
            Restaurants
          </NavLink>
          {user && <NavLink to="/orders">My orders</NavLink>}
          {user?.role === "admin" && <NavLink to="/admin">Admin</NavLink>}
          <span className="spacer" />
          {user ? (
            <>
              <span className="who">
                {user.full_name} · {user.role}
              </span>
              <button className="btn ghost small" onClick={logout}>
                Sign out
              </button>
            </>
          ) : (
            <NavLink to="/login">Sign in</NavLink>
          )}
        </div>
      </nav>
      <main className="container">
        <Outlet />
      </main>
    </>
  );
}
