import type { Account } from "./types";
export interface AccountPolicy {
  auto_raise: boolean;
  raise_interval: number;
  confirmation: boolean;
  confirmation_text: string;
  review_reply: boolean;
  review_chat: boolean;
  review_chat_text: string;
  review_reminder: boolean;
  review_reminder_text: string;
  confirmation_reminder: boolean;
  reminder_minutes: number;
  reminder_count: number;
  reminder_text: string;
}
export interface WorkspaceAccount extends Account {
  rating: string | null;
  review_count: number | null;
  today: Record<string, { sum: string; count: number }>;
  orders_in_work: number;
  automation: AccountPolicy;
  proxy_label: string;
  responder_enabled: boolean;
  ticket_enabled: boolean;
  review_master: boolean;
}
export interface ReplyRule {
  case_sensitive?: boolean;
  allow_arguments?: boolean;
  priority?: number;
  id: string;
  triggers: string[];
  reply: string;
  match: "contains" | "exact" | "starts_with";
  cooldown: number;
  account_cooldown?: number;
  buyer_cooldown?: number;
  active_days?: number[];
  active_from?: string | null;
  active_until?: string | null;
  enabled: boolean;
}
export interface ReplyConfig {
  dry_run: boolean;
  account_scope: "all" | "selected";
  account_ids: number[];
  greeting_enabled: boolean;
  greeting_text: string;
  greeting_mode: "once" | "daily";
  greeting_delay: number;
  paid_enabled: boolean;
  paid_text: string;
  paid_delay: number;
  keywords_enabled: boolean;
  keywords: ReplyRule[];
  commands_enabled: boolean;
  prefix: string;
  commands: ReplyRule[];
  reviews_enabled: boolean;
  review_target: "review" | "chat";
  review_delay: number;
  skip_empty_reviews: boolean;
  review_texts: Record<string, string>;
  refunds_enabled: boolean;
  ai_enabled: boolean;
  ai_provider: "openai";
  ai_model: string;
  ai_key: string;
  has_ai_key: boolean;
  ai_lot_ids: string[];
  ai_handoff_text: string;
}
export interface ResponderData {
  settings: ReplyConfig;
  enabled: boolean;
  configured: boolean;
  accounts: WorkspaceAccount[];
  tags: string[];
  handoffs: {
    chat_id: number;
    account_id: number;
    buyer: string;
    reason: string;
    at: string;
  }[];
  requests: {
    id: number;
    state: string;
    order_id: string;
    buyer: string;
    amount: string;
    currency: string;
  }[];
  events: {
    id: number;
    account_id: number;
    kind: string;
    state: string;
    created_at: string;
  }[];
}
