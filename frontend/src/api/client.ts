// A small typed fetch wrapper around the Lateral API.
//
// All requests are same-origin and relative (`/api/v1/...`): in production Nginx
// serves this SPA and proxies the API from the same host; in development Vite
// proxies the same paths. There is therefore no base URL and no CORS anywhere.

import type {
  MenuItem,
  Order,
  OrderStatus,
  Restaurant,
  TokenResponse,
  User,
} from "./types";

const BASE = "/api/v1";

/** An error carrying the HTTP status and the API's `detail` message. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let authToken: string | null = null;

/** Set (or clear) the bearer token attached to every subsequent request. */
export function setAuthToken(token: string | null): void {
  authToken = token;
}

async function parseError(response: Response): Promise<never> {
  let detail = `${response.status} ${response.statusText}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      detail = body.detail;
    } else if (Array.isArray(body.detail)) {
      // FastAPI 422 validation errors arrive as a list of objects.
      detail = body.detail
        .map((e) => (e as { msg?: string }).msg ?? "Invalid input")
        .join("; ");
    }
  } catch {
    // Response had no JSON body; keep the status-line fallback.
  }
  throw new ApiError(response.status, detail);
}

interface RequestOptions {
  method?: string;
  json?: unknown;
  form?: Record<string, string>;
  auth?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", json, form, auth = true } = options;
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;

  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form !== undefined) {
    headers["Content-Type"] = "application/x-www-form-urlencoded";
    body = new URLSearchParams(form).toString();
  }

  if (auth && authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  const response = await fetch(`${BASE}${path}`, { method, headers, body });

  if (!response.ok) {
    return parseError(response);
  }
  return (await response.json()) as T;
}

export const api = {
  // --- Auth ---------------------------------------------------------------
  register: (email: string, password: string, fullName: string) =>
    request<User>("/auth/register", {
      method: "POST",
      auth: false,
      json: { email, password, full_name: fullName },
    }),

  login: (email: string, password: string) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      auth: false,
      // OAuth2 password flow: form-encoded, and the field is named `username`.
      form: { username: email, password },
    }),

  me: () => request<User>("/auth/me"),

  // --- Catalogue ----------------------------------------------------------
  listRestaurants: () => request<Restaurant[]>("/restaurants", { auth: false }),

  getRestaurant: (id: number) =>
    request<Restaurant>(`/restaurants/${id}`, { auth: false }),

  listMenu: (restaurantId: number) =>
    request<MenuItem[]>(`/restaurants/${restaurantId}/menu`, { auth: false }),

  createRestaurant: (name: string, description: string | null) =>
    request<Restaurant>("/restaurants", {
      method: "POST",
      json: { name, description },
    }),

  addMenuItem: (
    restaurantId: number,
    item: { name: string; description: string | null; price_cents: number },
  ) =>
    request<MenuItem>(`/restaurants/${restaurantId}/menu`, {
      method: "POST",
      json: item,
    }),

  // --- Orders -------------------------------------------------------------
  placeOrder: (restaurantId: number, items: { menu_item_id: number; quantity: number }[]) =>
    request<Order>("/orders", {
      method: "POST",
      json: { restaurant_id: restaurantId, items },
    }),

  listOrders: (status?: OrderStatus) =>
    request<Order[]>(`/orders${status ? `?status=${status}` : ""}`),

  getOrder: (id: number) => request<Order>(`/orders/${id}`),

  updateOrderStatus: (id: number, status: OrderStatus) =>
    request<Order>(`/orders/${id}/status`, {
      method: "PATCH",
      json: { status },
    }),
};
