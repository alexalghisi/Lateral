// Wire types mirroring the FastAPI response schemas in `app/schemas/`.
// Kept in one place so a contract change surfaces as a compile error.

export type OrderStatus =
  | "pending"
  | "accepted"
  | "out_for_delivery"
  | "delivered"
  | "cancelled";

export type UserRole = "customer" | "admin";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface Restaurant {
  id: number;
  name: string;
  description: string | null;
  is_active: boolean;
}

export interface MenuItem {
  id: number;
  restaurant_id: number;
  name: string;
  description: string | null;
  price_cents: number;
  is_available: boolean;
}

export interface OrderLine {
  id: number;
  menu_item_id: number;
  item_name: string;
  unit_price_cents: number;
  quantity: number;
  subtotal_cents: number;
}

export interface Order {
  id: number;
  customer_id: number;
  restaurant_id: number;
  status: OrderStatus;
  total_cents: number;
  created_at: string;
  updated_at: string;
  items: OrderLine[];
}

// The lifecycle, mirrored from `app/domain/order_state.py`. Used to render the
// tracker and to offer only legal next actions in the admin view.
export const ORDER_FLOW: OrderStatus[] = [
  "pending",
  "accepted",
  "out_for_delivery",
  "delivered",
];

export const ALLOWED_TRANSITIONS: Record<OrderStatus, OrderStatus[]> = {
  pending: ["accepted", "cancelled"],
  accepted: ["out_for_delivery", "cancelled"],
  out_for_delivery: ["delivered"],
  delivered: [],
  cancelled: [],
};

export const STATUS_LABELS: Record<OrderStatus, string> = {
  pending: "Pending",
  accepted: "Accepted",
  out_for_delivery: "Out for delivery",
  delivered: "Delivered",
  cancelled: "Cancelled",
};
