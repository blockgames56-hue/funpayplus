export type Product = {
  id: number;
  name: string;
  available_accounts: number;
  price_per_hour_rub: number;
  min_hours: number;
  max_hours: number;
};
export type KoConfig = {
  enabled: boolean;
  has_api_key: boolean;
  api_key?: string;
  currency: "RUB" | "USD";
  only_verified: boolean;
  auto_hide: boolean;
  auto_delivery: boolean;
  review_bonus: boolean;
  allow_loss: boolean;
  balance_threshold: number;
  balance_currency: "RUB" | "USD";
  hide_low_balance: boolean;
  account_scope: "all" | "selected";
  account_ids: number[];
  stock_interval: number;
  game_intervals: Record<string, number>;
};
export type Mapping = {
  id: number;
  lot_id: number;
  funpay_item_id: string;
  account_id: number;
  node_id: number;
  title: string;
  price: string;
  product_id: number;
  hours: number;
  verified: boolean;
  is_active: boolean;
  lot_active: boolean;
  policy: { auto_price?: boolean; markup?: number };
};
export type Rental = {
  id: number;
  order_id: string;
  buyer: string;
  uid: string | null;
  status: string;
  end_time: string | null;
  last_error: string | null;
  login: string;
  hours: number;
  title: string;
  account_id: number;
  remaining: number;
  queue: { id: number; kind: string; state: string; attempts: number }[];
};
export type Texts = {
  title: string;
  description: string;
  payment: string;
  title_en: string;
  description_en: string;
  payment_en: string;
};
export type StudioForm = Texts & {
  source_lot_id: number;
  product_ids: number[];
  sources: Record<string, number>;
  hours: number[];
  currency: "RUB";
  price_mode: "markup" | "fixed";
  markup: number;
  fixed_price: number;
  amount: number;
};
export type StudioTemplate = Texts & {
  id: string;
  name: string;
  hours: number[];
  price_mode: "markup" | "fixed";
  markup: number;
  fixed_price: number;
  amount: number;
};
export type Batch = {
  id: string;
  state: string;
  rows: {
    product_id: number;
    game: string;
    hours: number;
    cost: string;
    price: string;
    stock: number;
    state: string;
    error?: string;
    funpay_item_id?: string;
    texts: Texts;
  }[];
};
export type KoState = {
  settings: KoConfig;
  catalog: { items: Product[]; updated_at: string | null };
  balance: {
    balance_rub: number;
    balance_usd: number;
    username: string;
  } | null;
  accounts: {
    id: number;
    title: string;
    username: string;
    is_active: boolean;
  }[];
  bindings: Mapping[];
  rentals: Rental[];
  manual_orders: {
    id: number;
    number: string;
    title: string;
    buyer: string;
    account_id: number;
  }[];
  operations: {
    id: number;
    rental_id: number;
    kind: string;
    state: string;
    hours: number;
  }[];
  finance: {
    currencies: Record<string, { revenue: string; expenses: string }>;
    unconfirmed_costs: number;
  };
  studio_defaults: Texts;
  studio_tags: string[];
  sync: { updated_at?: string; changed?: number; price_check?: boolean };
};
