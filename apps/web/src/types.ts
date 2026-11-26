export interface Account {
  id: number;
  title: string;
  funpay_id: number;
  username: string;
  avatar_url: string | null;
  balance_rub: string;
  balance_usd: string;
  balance_eur: string;
  active_sales: number;
  active_purchases: number;
  status: 'online' | 'offline' | 'error' | 'banned';
  last_error: string | null;
  is_active: boolean;
  proxy_id: number | null;
  last_synced_at: string | null;
}
export interface ProxyServer {
  id: number;
  protocol: string;
  host: string;
  port: number;
  username: string | null;
  country_code: string | null;
  ping_ms: number | null;
  status: string;
  last_checked_at: string | null;
}
export interface Chat {
  id: number;
  account_id: number;
  funpay_chat_id: string;
  interlocutor_id: number;
  interlocutor_name: string;
  interlocutor_avatar: string | null;
  last_message_text: string | null;
  last_message_at: string;
  unread_count: number;
  current_order_id: string | null;
  notes: string;
  is_pinned: boolean;
}
export interface Message {
  id: number;
  chat_id: number;
  text: string;
  sender_name: string;
  is_my: boolean;
  has_image: boolean;
  image_url: string | null;
  created_at: string;
}
export interface Lot {
  id: number;
  account_id: number;
  funpay_lot_id: string;
  node_id: number;
  category_title: string;
  title: string;
  price: string;
  amount: number | null;
  is_active: boolean;
  auto_raise: boolean;
  last_raised_at: string | null;
  next_raise_at: string | null;
}
export interface Rule {
  id: number;
  account_id: number;
  lot_title_pattern: string;
  match_regex: boolean;
  delivery_type: 'pool_keys' | 'static_text';
  message_template: string;
  is_active: boolean;
  remaining: number;
}
export interface Blacklist {
  id: number;
  user_id: number;
  username: string;
  reason: string;
  scope: 'global' | 'account' | 'chat';
  account_id: number | null;
  chat_id: number | null;
  created_at: string;
}
export interface Order {
  id: number;
  account_id: number;
  funpay_order_id: string;
  description: string;
  sum: string;
  currency: string;
  buyer_id: number;
  buyer_name: string;
  status: string;
  auto_delivered: boolean;
  delivery_state: string;
  created_at: string;
}
export interface Activity {
  id: number;
  account_id: number | null;
  channel: string;
  text: string;
  level: string;
  created_at: string;
}
export type Money = Record<'RUB' | 'USD' | 'EUR', string>;
export interface Dashboard {
  balances: Money;
  today: Money;
  today_count: number;
  monthly_net: Money;
  commission_percent: number;
  online: number;
  total: number;
  errors: number;
  series: { label: string; RUB: number; USD: number; EUR: number }[];
  activities: Activity[];
}
export interface Health {
  ok: boolean;
  database: string;
  database_ms: number;
  workers: number;
  scheduler: boolean;
  subscribers: number;
}
export interface AppSettings {
  commission_percent: number;
  timezone: string;
  auto_raise_interval: number;
  sounds: boolean;
  compact: boolean;
}
export interface Plugins {
  responder: boolean;
    reply_delay_min: number;
    reply_delay_max: number;
    auto_ticket: boolean;
    ticket_templates: { pattern: string; reply: string; is_active: boolean }[];
  first_message: string;
  keywords: string;
  keyword_reply: string;
  night_start: number;
  night_end: number;
  night_reply: string;
  offline_activity: boolean;
  auto_thanks: boolean;
  thanks_text: string;
  review_replies: Record<string, string>;
  commands: { trigger: string; reply: string; match: 'exact' | 'contains'; is_active: boolean }[];
}
export interface Telegram {
  control_enabled?: boolean;
  mini_app_url?: string;
  token: string;
  has_token?: boolean;
  chat_id: string;
  enabled: boolean;
  events: string[];
}
