import { requestId } from "./request-id";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  BookOpen,
  RefreshCw,
  MessageSquare,
  ShoppingBag,
  CircleCheck,
  Command,
  Star,
  ScrollText,
  Sparkles,
  Plus,
  Pencil,
  Trash2,
  Eye,
  Play,
  Square,
  RotateCcw,
} from "lucide-react";
import { date, money, request, useAction, useData } from "./api";
import { Button, Empty, Failure, Field, Modal, Pending, Toggle } from "./ui";
import { PolicyEditor } from "./account-workspace";
import type { WorkspaceAccount, ReplyRule } from "./workspace-types";
import "./workspaces.css";
import { AutoSaveStatus, useAutoSave } from "./auto-save";
interface ReplyConfig {
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
  has_ai_key?: boolean;
  ai_lot_ids: string[];
  ai_handoff_text: string;
}
interface Refund {
  id: number;
  state: string;
  order_id: string;
  buyer: string;
  amount: string;
  currency: string;
  account_id: number;
}
interface Workspace {
  settings: ReplyConfig;
  enabled: boolean;
  reviews_running: boolean;
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
  requests: Refund[];
  events: {
    id: number;
    kind: string;
    state: string;
    account_id: number;
    chat_id: number;
    created_at: string;
    due_at: string;
  }[];
}
const tabs = [
  {
    id: "greeting",
    title: "Приветствие",
    hint: "Первое обращение покупателя",
    icon: MessageSquare,
  },
  {
    id: "paid",
    title: "После оплаты",
    hint: "Новый оплаченный заказ",
    icon: ShoppingBag,
  },
  {
    id: "confirmation",
    title: "Подтверждение заказа",
    hint: "Ответы и напоминания",
    icon: CircleCheck,
  },
  {
    id: "commands",
    title: "Команды",
    hint: "Префикс и псевдонимы",
    icon: Command,
  },
  {
    id: "reviews",
    title: "Ответы на отзывы",
    hint: "Шаблоны для пяти оценок",
    icon: Star,
  },
  {
    id: "ai",
    title: "AI-консультант",
    hint: "Ответы по открытому лоту",
    icon: Sparkles,
  },
  { id: "logs", title: "Логи", hint: "История отправки", icon: ScrollText },
];
const stateLabels: Record<string, string> = {
  pending: "В очереди",
  sending: "Отправляется",
  sent: "Отправлено",
  uncertain: "Требует проверки",
  cancelled: "Отменено",
  awaiting_owner: "Ожидает решения",
  refunded: "Возврат выполнен",
  rejected: "Отклонено",
  observed: "Проверено",
  dry_run: "Тест: без отправки",
};
function Tags({
  tags,
  onInsert,
}: {
  tags: string[];
  onInsert: (tag: string) => void;
}) {
  return (
    <div className="ws-actions head-actions">
      {tags.map((tag) => (
        <button
          type="button"
          className="ws-tag"
          title={`Вставить {${tag}}`}
          key={tag}
          onClick={() => onInsert(`{${tag}}`)}
        >{`{${tag}}`}</button>
      ))}
    </div>
  );
}
function RuleEditor({ rule, onSave, onClose, status }: { rule: ReplyRule; command: boolean; onSave: (rule: ReplyRule) => void; onClose: () => void; status: ReturnType<typeof useAutoSave> }) {
  const [draft, setDraft] = useState(rule);
  const [triggers, setTriggers] = useState(rule.triggers.join(", "));
  function update(next: ReplyRule, text = triggers) {
    setDraft(next); setTriggers(text);
    const aliases = text.split(",").map(value => value.trim()).filter(Boolean);
    if (aliases.length && next.reply.trim()) onSave({ ...next, triggers: aliases, match: "exact", case_sensitive: false, allow_arguments: true, priority: 0, cooldown: 10, account_cooldown: 0, buyer_cooldown: 0, active_days: [0,1,2,3,4,5,6], active_from: null, active_until: null });
  }
  return <Modal open onOpenChange={open => { if (!open) onClose(); }} title="Команда" description="Укажите варианты через запятую и текст ответа. Изменения применяются автоматически.">
    <div className="ws-form"><Field label="Команда и варианты"><input aria-label="Варианты срабатывания" value={triggers} maxLength={3000} onChange={e => update(draft, e.target.value)} placeholder="помощь, help" /></Field>
    <Field label="Ответ"><textarea aria-label="Ответ" rows={6} maxLength={5000} value={draft.reply} onChange={e => update({ ...draft, reply: e.target.value })} /><Tags tags={["buyer_name","seller_name","date","time"]} onInsert={tag => update({ ...draft, reply: draft.reply + tag })} /></Field>
    <div className="ws-row"><strong>Команда включена</strong><Toggle label="Правило включено" checked={draft.enabled} onChange={enabled => update({ ...draft, enabled })} /></div>
    <small>Регистр не учитывается. Защита от повтора: 10 секунд.</small>{(!triggers.trim() || !draft.reply.trim()) ? <small>Заполните команду и ответ для применения.</small> : <AutoSaveStatus state={status} />}<Button secondary onClick={onClose}>Готово</Button></div></Modal>;
}
export default function ResponderWorkspace({
  initialTab = "greeting",
}: {
  initialTab?: string;
}) {
  const query = useData<Workspace>(
      "responder-workspace",
      "/workspaces/responder",
    ),
    action = useAction();
  const [tab, setTab] = useState(tabs.some((t) => t.id === initialTab) ? initialTab : "greeting"),
    [draft, setDraft] = useState<ReplyConfig | null>(null),
    [accountId, setAccountId] = useState(""),
    [editing, setEditing] = useState<ReplyRule | null>(null),
    [preview, setPreview] = useState<string | null>(null),
    [docs, setDocs] = useState(false),
    [refund, setRefund] = useState<Refund | null>(null),
    [logFilter, setLogFilter] = useState("all");
  const [dirty, setDirty] = useState(false);
  const data = query.data,
    cfg = draft || data?.settings;
  function set<K extends keyof ReplyConfig>(key: K, value: ReplyConfig[K]) {
    if (cfg) { setDraft({ ...cfg, [key]: value }); setDirty(true); }
  }
  const normalized = cfg ? { ...cfg, greeting_delay: 0, paid_delay: 0, review_delay: 0 } : null;
  const auto = useAutoSave({ value: normalized, dirty: dirty || !!cfg && (cfg.greeting_delay > 0 || cfg.paid_delay > 0 || cfg.review_delay > 0), enabled: !!data,
    save: value => request("/workspaces/responder", "PUT", value), onSaved: () => setDirty(false), queryKeys: ["responder-workspace"] });
  async function save() { if (normalized) { await request("/workspaces/responder", "PUT", normalized); setDirty(false); } }
  const reviewOnly = initialTab === "reviews";
  const enabled = reviewOnly
    ? Boolean(query.data?.reviews_running)
    : Boolean(query.data?.enabled);
  const controlId = reviewOnly ? "reviews" : "responder";
  async function toggle() {
    if (!data) return;
    if (reviewOnly && !enabled && !cfg?.reviews_enabled) {
      await action.mutateAsync({
        path: "/workspaces/responder",
        method: "PUT",
        body: { ...cfg, reviews_enabled: true },
      });
      setDraft(null);
    } else if (!enabled && !data.configured) await save();
    await action.mutateAsync({
      path: `/workspaces/automation/${controlId}`,
      method: "PUT",
      body: { is_enabled: !enabled },
    });
  }
  const account =
    data?.accounts.find((a) => String(a.id) === accountId) || data?.accounts[0];
  if (query.isPending) return <Pending />;
  if (query.isError || !data || !cfg)
    return <Failure error={query.error} retry={() => void query.refetch()} />;
  const toggleRow = (
    key:
      | "dry_run"
      | "greeting_enabled"
      | "paid_enabled"
      | "keywords_enabled"
      | "commands_enabled"
      | "reviews_enabled"
      | "skip_empty_reviews"
      | "refunds_enabled"
      | "ai_enabled",
    title: string,
    hint?: string,
  ) => (
    <div className="ws-row">
      <div>
        <strong>{title}</strong>
        {hint && <small>{hint}</small>}
      </div>
      <Toggle label={title} checked={cfg[key]} onChange={(v) => set(key, v)} />
    </div>
  );
  const message = (
    key: "greeting_text" | "paid_text" | "ai_handoff_text",
    label: string,
    tags = data.tags,
  ) => (
    <Field label={label}>
      <textarea
        aria-label={label}
        maxLength={5000}
        value={cfg[key]}
        onChange={(e) => set(key, e.target.value)}
      />
      <Tags tags={tags} onInsert={(tag) => set(key, cfg[key] + tag)} />
    </Field>
  );
  const selectAccount = (
    <Field label="Аккаунт FunPay">
      <select
        aria-label="Аккаунт настроек"
        value={account?.id || ""}
        onChange={(e) => setAccountId(e.target.value)}
      >
        {data.accounts.map((a) => (
          <option key={a.id} value={a.id}>
            {a.username} · #{a.funpay_id}
          </option>
        ))}
      </select>
    </Field>
  );
  return (
    <div className="workspace ws-form">

      <details className="ws-panel ws-pad"><summary>Проверка без отправки</summary>{toggleRow("dry_run", "Тестовый режим", "Включайте только для проверки: сообщения не отправляются.")}</details>
      <div className="page-head">
        <div>
          <span className="eyebrow">Автоматизация сообщений</span>
          <h1>
            {reviewOnly ? "Ответы на отзывы" : "Автоответчик"}{" "}
            <span className={`ws-badge ${enabled ? "good" : ""}`}>
              <i />
              {enabled ? "Запущен" : "Остановлен"}
            </span>
          </h1>
          <p>Приветствия, команды, ответы на заказы и отзывы</p>
        </div>
        <div className="ws-actions head-actions">
          <Link className="button secondary" to="/accounts">
            Сообщения по аккаунтам
          </Link>
          <Button secondary onClick={() => setDocs(true)}>
            <BookOpen size={14} />
            Документация
          </Button>
          <Button
            secondary
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <RefreshCw size={14} />
            Обновить
          </Button>
          <Button
            secondary
            disabled={action.isPending}
            onClick={() => void toggle().catch(() => {})}
          >
            {enabled ? <Square size={14} /> : <Play size={14} />}
            {enabled ? "Остановить" : "Запустить"}
          </Button>
          {enabled && (
            <Button
              secondary
              disabled={action.isPending}
              onClick={() =>
                void (async () => {
                  await action.mutateAsync({
                    path: `/workspaces/automation/${controlId}`,
                    method: "PUT",
                    body: { is_enabled: false },
                  });
                  await action.mutateAsync({
                    path: `/workspaces/automation/${controlId}`,
                    method: "PUT",
                    body: { is_enabled: true },
                    success: "Автоответчик перезапущен",
                  });
                })().catch(() => {})
              }
            >
              <RotateCcw size={14} />
              Рестарт
            </Button>
          )}
        </div>
      </div>
      <nav className="reply-nav" aria-label="Настройки автоответчика">
        {tabs.map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? "active" : ""}
            onClick={() => setTab(t.id)}
          >
            <t.icon size={21} />
            <span>
              <strong>{t.title}</strong>
              <small>
                {t.id === "commands"
                  ? `${cfg.commands.length} команд · ${cfg.prefix}`
                  : t.hint}
              </small>
            </span>
          </button>
        ))}
      </nav>
      <AutoSaveStatus state={auto} />
      {!["confirmation", "logs"].includes(tab) && (
        <details className="ws-panel ws-pad">
          <summary>
            Аккаунты автоответчика ·{" "}
            {cfg.account_scope === "all"
              ? "все подключённые"
              : `выбрано ${cfg.account_ids.length}`}
          </summary>
          <div className="ws-form" style={{ marginTop: 15 }}>
            <div className="segmented">
              <button
                className={cfg.account_scope === "all" ? "active" : ""}
                onClick={() => set("account_scope", "all")}
              >
                Все аккаунты
              </button>
              <button
                className={cfg.account_scope === "selected" ? "active" : ""}
                onClick={() => set("account_scope", "selected")}
              >
                Выбранные аккаунты
              </button>
            </div>
            {cfg.account_scope === "selected" &&
              data.accounts.map((a) => (
                <label key={a.id} className="ws-check">
                  <input
                    type="checkbox"
                    checked={cfg.account_ids.includes(a.id)}
                    onChange={(e) =>
                      set(
                        "account_ids",
                        e.target.checked
                          ? [...cfg.account_ids, a.id]
                          : cfg.account_ids.filter((id) => id !== a.id),
                      )
                    }
                  />
                  {a.username}
                </label>
              ))}
            <small>
              Ответы после подтверждения и напоминания настраиваются отдельно
              для каждого аккаунта.
            </small>
          </div>
        </details>
      )}
      {tab === "greeting" && (
        <section className="ws-panel ws-pad ws-form">
          {toggleRow(
            "greeting_enabled",
            "Приветственное сообщение",
            "Отправляется покупателю, когда он пишет вам в чат",
          )}
          {message(
            "greeting_text",
            "Приветственное сообщение",
            data.tags.filter((t) => !["stars", "review_text"].includes(t)),
          )}
          <small>
            Случайные варианты разделяйте |. Для самого символа используйте \|.
          </small>
          <div className="ws-grid">
            <Field label="Когда отправлять">
              <div className="segmented">
                <button
                  className={cfg.greeting_mode === "once" ? "active" : ""}
                  onClick={() => set("greeting_mode", "once")}
                >
                  Один раз каждому
                </button>
                <button
                  className={cfg.greeting_mode === "daily" ? "active" : ""}
                  onClick={() => set("greeting_mode", "daily")}
                >
                  Раз в 24 часа
                </button>
              </div>
            </Field>
          </div>
        </section>
      )}
      {tab === "paid" && (
        <section className="ws-panel ws-pad ws-form">
          {toggleRow(
            "paid_enabled",
            "Сообщение после оплаты",
            "Отправляется после получения нового оплаченного заказа",
          )}
          {message(
            "paid_text",
            "Сообщение после оплаты",
            data.tags.filter((t) => !["stars", "review_text"].includes(t)),
          )}
          <div className="ws-grid">
            <div className="ws-review">
              <strong>Не больше одного раза на заказ</strong>
              <small>История отправки сохраняется после перезапуска.</small>
            </div>
          </div>
          <p className="ws-hint">
            Встроенный текст лота FunPay отправляется самим FunPay. Если
            заполнены оба сообщения, покупатель получит оба.
          </p>
        </section>
      )}
      {tab === "confirmation" && (
        <>
          {selectAccount}
          {account ? (
            <PolicyEditor
              key={account.id}
              account={account}
              only="confirmation"
            />
          ) : (
            <Empty
              title="Нет аккаунтов"
              text="Добавьте аккаунт для настройки ответов."
            />
          )}
        </>
      )}
      {(tab === "commands") && (
        <section className="ws-panel ws-pad ws-form">
          <div className="ws-row">
            <div>
              <h3>Команды</h3>
              <small>
                {tab === "commands"
                  ? "Покупатель пишет команду и получает ответ"
                  : "Регистр не важен. Первое подходящее правило имеет приоритет."}
              </small>
            </div>
            <div className="ws-actions head-actions">
              <Button
                secondary
                onClick={() =>
                  setEditing({
                    id: requestId(),
                    triggers: [],
                    reply: "",
                    match: tab === "commands" ? "exact" : "contains",
                    cooldown: 60,
                    enabled: true,
                  })
                }
              >
                <Plus size={14} />
                {tab === "commands" ? "Добавить команду" : "Добавить фразу"}
              </Button>
              <Toggle
                label="Команды включены"
                checked={cfg.commands_enabled}
                onChange={(v) => set("commands_enabled", v)}
              />
            </div>
          </div>
          {tab === "commands" && (
            <>
              <Field label="Префикс команд">
                <input
                  style={{ maxWidth: 100 }}
                  maxLength={3}
                  value={cfg.prefix}
                  onChange={(e) => set("prefix", e.target.value)}
                />
              </Field>
              {toggleRow(
                "refunds_enabled",
                `Заявки на возврат: ${cfg.prefix}возврат`,
                "Создаёт заявку продавцу. Возврат выполняется только после вашего решения в приложении или личном Telegram.",
              )}
            </>
          )}
          {cfg[tab].map((rule) => (
            <div className="reply-rule" key={rule.id}>
              <div>
                <div className="ws-actions head-actions">
                  {rule.triggers.map((t) => (
                    <span key={t} className="ws-tag">
                      {tab === "commands" && !t.startsWith(cfg.prefix) ? cfg.prefix : ""}
                      {t}
                    </span>
                  ))}
                  {!rule.enabled && <span className="ws-badge">Выключено</span>}
                </div>
                <p>{rule.reply}</p>
                <small>
                  {
                    {
                      contains: "Содержит",
                      exact: "Точное совпадение",
                      starts_with: "Начинается с",
                    }[rule.match]
                  }{" "}
                  · Повтор через {rule.cooldown} сек.
                </small>
              </div>
              <div className="ws-actions head-actions">
                <Button
                  secondary
                  aria-label="Редактировать правило"
                  onClick={() => setEditing(rule)}
                >
                  <Pencil size={14} />
                </Button>
                <Button
                  secondary
                  aria-label="Удалить правило"
                  onClick={() =>
                    set(
                      tab,
                      cfg[tab].filter((r) => r.id !== rule.id),
                    )
                  }
                >
                  <Trash2 size={14} />
                </Button>
              </div>
            </div>
          ))}
          {!cfg[tab].length && (
            <div className="ws-empty">Добавьте первое правило</div>
          )}
        </section>
      )}
      {tab === "commands" && (
        <section className="ws-panel ws-pad ws-form">
          <h3>Заявки на возврат</h3>
          {data.requests.length ? (
            data.requests.map((r) => (
              <div className="ws-row" key={r.id}>
                <div>
                  <strong>
                    #{r.order_id} · {r.buyer} · {money(r.amount, r.currency)}
                  </strong>
                  <small>{stateLabels[r.state] || r.state}</small>
                </div>
                <div className="ws-actions head-actions">
                  {r.state === "awaiting_owner" && (
                    <>
                      <Button
                        secondary
                        disabled={action.isPending}
                        onClick={() =>
                          action.mutate({
                            path: `/workspaces/responder/refunds/${r.id}`,
                            body: { action: "reject" },
                            success: "Заявка отклонена",
                          })
                        }
                      >
                        Отклонить
                      </Button>
                      <Button
                        disabled={action.isPending}
                        onClick={() => setRefund(r)}
                      >
                        Сделать возврат
                      </Button>
                    </>
                  )}
                  {r.state === "uncertain" && (
                    <Button
                      secondary
                      disabled={action.isPending}
                      onClick={() =>
                        action.mutate({
                          path: `/workspaces/responder/refunds/${r.id}/reconcile`,
                        })
                      }
                    >
                      Сверить с FunPay
                    </Button>
                  )}
                  <a
                    className="ws-link"
                    href={`https://funpay.com/orders/${encodeURIComponent(r.order_id)}/`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Заказ
                  </a>
                </div>
              </div>
            ))
          ) : (
            <small>Новых заявок нет</small>
          )}
        </section>
      )}
      {tab === "reviews" && (
        <section className="ws-panel ws-pad ws-form">
          {toggleRow(
            "reviews_enabled",
            "Автоответ на отзывы",
            "Свой текст для каждой оценки. Пустой шаблон пропускается.",
          )}
          <Field label="Куда отправлять ответ">
            <div className="segmented">
              <button
                className={cfg.review_target === "review" ? "active" : ""}
                onClick={() => set("review_target", "review")}
              >
                Под отзывом
              </button>
              <button
                className={cfg.review_target === "chat" ? "active" : ""}
                onClick={() => set("review_target", "chat")}
              >
                В чат покупателю
              </button>
            </div>
          </Field>
          <div className="ws-grid">
            {toggleRow(
              "skip_empty_reviews",
              "Пропускать пустые отзывы",
              "Не отвечать на оценку без текста",
            )}
          </div>
          {[5, 4, 3, 2, 1].map((stars) => (
            <div className="ws-review" key={stars}>
              <div className="ws-row">
                <span className="ws-stars" aria-label={`${stars} звёзд`}>
                  {"★".repeat(stars)}
                  {"☆".repeat(5 - stars)}
                </span>
                <Button
                  secondary
                  onClick={() =>
                    setPreview(cfg.review_texts[String(stars)] || "")
                  }
                >
                  <Eye size={14} />
                  Превью
                </Button>
              </div>
              <textarea
                aria-label={`Ответ на ${stars} звёзд`}
                maxLength={5000}
                value={cfg.review_texts[String(stars)] || ""}
                onChange={(e) =>
                  set("review_texts", {
                    ...cfg.review_texts,
                    [String(stars)]: e.target.value,
                  })
                }
              />
              <Tags
                tags={[
                  "buyer_name",
                  "date",
                  "time",
                  "order_id",
                  "lot_name",
                  "lot_price",
                  "stars",
                  "review_text",
                ]}
                onInsert={(tag) =>
                  set("review_texts", {
                    ...cfg.review_texts,
                    [String(stars)]:
                      (cfg.review_texts[String(stars)] || "") + tag,
                  })
                }
              />
            </div>
          ))}
          <p className="ws-hint">
            Для конкретного аккаунта ответы на отзывы также можно включить в его
            настройках. Отправки проверяются по истории заказа, чтобы не
            повторять ответ.
          </p>
        </section>
      )}
      {tab === "ai" && (
        <>
          <section className="ws-panel ws-pad ws-form">
            <h3>AI-консультант</h3>
            <p>
              Отвечает на простые вопросы по подтверждённому открытому лоту. При
              конфликте или нехватке информации передаёт диалог вам.
            </p>
            <p className="ws-hint">
              При включении в OpenAI передаются вопрос покупателя, до трёх
              предыдущих вопросов и ответов AI, название, описание и цена лота.
              Используется ваш API-ключ; стоимость определяет провайдер.
            </p>
            {toggleRow("ai_enabled", "Отвечать с помощью AI")}
            <div className="ws-grid">
              <Field label="Провайдер">
                <select value="openai" aria-label="AI-провайдер">
                  <option value="openai">OpenAI</option>
                </select>
              </Field>
              <Field label="Модель">
                <input
                  maxLength={100}
                  value={cfg.ai_model}
                  onChange={(e) => set("ai_model", e.target.value)}
                  placeholder="Имя модели из кабинета провайдера"
                />
              </Field>
            </div>
            <Field
              label="API-ключ"
              hint={
                cfg.has_ai_key
                  ? "Ключ сохранён. Пустое поле сохраняет текущий ключ."
                  : "Хранится в зашифрованном виде"
              }
            >
              <input
                autoComplete="new-password"
                type="password"
                value={cfg.ai_key}
                onChange={(e) => set("ai_key", e.target.value)}
                placeholder={
                  cfg.has_ai_key
                    ? "•••••••• — ключ сохранён"
                    : "Ключ выбранного провайдера"
                }
              />
            </Field>
            <Field
              label="Только эти лоты"
              hint="Пустое поле: все подтверждённые открытые лоты выбранных аккаунтов"
            >
              <input
                value={cfg.ai_lot_ids.join(", ")}
                onChange={(e) =>
                  set(
                    "ai_lot_ids",
                    e.target.value
                      .split(",")
                      .map((v) => v.trim())
                      .filter(Boolean),
                  )
                }
                placeholder="ID лотов через запятую"
              />
            </Field>
            {message("ai_handoff_text", "Сообщение при передаче продавцу", [])}
            <small>Команды и ключевые фразы имеют приоритет перед AI.</small>
          </section>
          <section className="ws-panel ws-pad ws-form">
            <div className="ws-row">
              <div>
                <h3>Диалоги у продавца</h3>
                <small>
                  Пауза сохраняется после перезапуска и выключения AI, пока вы
                  её не снимете.
                </small>
              </div>
              <Button secondary onClick={() => void query.refetch()}>
                Обновить диалоги
              </Button>
            </div>
            {data.handoffs.length ? (
              data.handoffs.map((h) => (
                <div className="ws-row" key={h.chat_id}>
                  <div>
                    <strong>
                      {h.buyer} · Аккаунт #{h.account_id}
                    </strong>
                    <small>
                      {h.reason} · {date(h.at)}
                    </small>
                  </div>
                  <div className="ws-actions head-actions">
                    <Link
                      className="ws-link"
                      to={`/accounts/${h.account_id}?tab=chats`}
                    >
                      Открыть чаты
                    </Link>
                    <Button
                      secondary
                      disabled={action.isPending}
                      onClick={() =>
                        action.mutate({
                          path: `/workspaces/responder/handoffs/${h.chat_id}/resume`,
                          success: "Автоответчик в диалоге возобновлён",
                        })
                      }
                    >
                      Возобновить
                    </Button>
                  </div>
                </div>
              ))
            ) : (
              <small>Переданных диалогов пока нет</small>
            )}
          </section>
        </>
      )}
      {tab === "logs" && (
        <section className="ws-panel">
          <div className="ws-toolbar">
            <h3>Журнал автоответчика</h3>
            <select
              aria-label="Фильтр журнала"
              value={logFilter}
              onChange={(e) => setLogFilter(e.target.value)}
            >
              <option value="all">Все отправки</option>
              <option value="uncertain">Требует проверки</option>
              <option value="pending">В очереди</option>
              <option value="sent">Отправлено</option>
            </select>
            <Button secondary onClick={() => void query.refetch()}>
              Обновить
            </Button>
          </div>
          <div className="ws-table-scroll">
            <table className="ws-table">
              <thead>
                <tr>
                  {["Дата", "Аккаунт", "Тип", "Результат", "Диалог"].map(
                    (t) => (
                      <th key={t}>{t}</th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {data.events
                  .filter((e) => logFilter === "all" || e.state === logFilter)
                  .map((e) => (
                    <tr key={e.id}>
                      <td>{date(e.created_at)}</td>
                      <td>#{e.account_id}</td>
                      <td>
                        {(
                          {
                            greeting: "Приветствие",
                            paid: "После оплаты",
                            keyword: "Ключевая фраза",
                            command: "Команда",
                            review: "Отзыв",
                            review_chat: "Сообщение после отзыва",
                            confirmation: "Подтверждение",
                            confirmation_reminder: "Напоминание",
                            ai: "AI",
                            refund: "Заявка на возврат",
                          } as Record<string, string>
                        )[e.kind] || e.kind}
                      </td>
                      <td>
                        <span
                          className={`ws-badge ${e.state === "sent" ? "good" : e.state === "uncertain" ? "bad" : ""}`}
                        >
                          {stateLabels[e.state] || e.state}
                        </span>
                      </td>
                      <td>
                        <Link to={`/accounts/${e.account_id}?tab=chats`}>
                          #{e.chat_id}
                        </Link>
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {!data.events.length && (
              <div className="ws-empty">Записей пока нет</div>
            )}
          </div>
          <p className="ws-pad ws-hint">
            Если результат отправки неизвестен, проверьте его на FunPay.
            Повторная отправка автоматически не выполняется.
          </p>
        </section>
      )}
      {editing && (tab === "commands") && (
        <RuleEditor
          key={editing.id}
          status={auto}
          rule={editing}
          command={tab === "commands"}
          onClose={() => setEditing(null)}
          onSave={(r) => {
            setDraft(current => { const base = current || data.settings; const rows = base.commands; return { ...base, commands: rows.some(row => row.id === r.id) ? rows.map(row => row.id === r.id ? r : row) : [...rows, r] }; });
            setDirty(true);
          }}
        />
      )}
      <Modal
        open={preview !== null}
        onOpenChange={(v) => {
          if (!v) setPreview(null);
        }}
        title="Предпросмотр ответа"
        description="Пример с подстановкой переменных; сообщение покупателю не отправляется."
      >
        <p style={{ whiteSpace: "pre-wrap", lineHeight: 1.7 }}>
          {preview?.replace(
            /\{([^}]+)\}/g,
            (_, key: string) =>
              (
                ({
                  buyer_name: "Покупатель",
                  date: "16.11.2026 12:00",
                  date_only: "16.11.2026",
                  time: "12:00",
                  order_id: "EXAMPLE",
                  lot_name: "Название лота",
                  lot_price: "100",
                  stars: "5",
                  review_text: "Спасибо!",
                }) as Record<string, string>
              )[key] || `[${key}]`,
          ) || "Пустой шаблон — ответ не отправляется"}
        </p>
      </Modal>
      <Modal
        open={docs}
        onOpenChange={setDocs}
        title="Как работает автоответчик"
        description="Настройки применяются автоматически."
      >
        <div className="ws-form">
          <p>
            Выберите аккаунты, настройте тексты и запустите плагин. Команды
            обрабатываются перед ключевыми фразами, затем приветствием и AI.
          </p>
          <p>
            Ответы после подтверждения и напоминания принадлежат выбранному
            аккаунту и доступны в его карточке. Уже обработанные заказы и
            сообщения не отправляются повторно.
          </p>
          <p>
            При остановке аккаунта его очередь приостанавливается. Перед
            отправкой по заказу приложение проверяет статус и покупателя на
            FunPay. Исторические заказы не запускают новые автоматические
            сообщения.
          </p>
          <p>
            Заявка на возврат требует решения владельца. После неопределённого
            результата повторный финансовый запрос блокируется.
          </p>
        </div>
      </Modal>
      <Modal
        open={!!refund}
        onOpenChange={(v) => {
          if (!v) setRefund(null);
        }}
        title="Подтвердить возврат"
        description="Деньги по этому заказу будут возвращены покупателю на FunPay."
      >
        <div className="ws-form">
          <strong>
            #{refund?.order_id} · {refund?.buyer} ·{" "}
            {refund && money(refund.amount, refund.currency)}
          </strong>
          <Button
            disabled={action.isPending}
            onClick={() =>
              void action
                .mutateAsync({
                  path: `/workspaces/responder/refunds/${refund?.id}`,
                  body: { action: "approve" },
                  success: "Возврат подтверждён FunPay",
                })
                .then(() => setRefund(null))
                .catch(() => {})
            }
          >
            Вернуть деньги покупателю
          </Button>
        </div>
      </Modal>
    </div>
  );
}
