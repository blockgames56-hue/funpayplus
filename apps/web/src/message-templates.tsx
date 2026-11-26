import { useState } from "react";
import { RotateCcw, Search, MessageSquareText, Eye } from "lucide-react";
import { toast } from "sonner";
import { request, useData } from "./api";
import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { Button } from "./ui";

type Template = {
  key: string;
  title: string;
  text: string;
  default: string;
  variables: string[];
  group: string;
  audience: string;
  custom: boolean;
  condition: string;
};
const examples: Record<string, string> = {
  order_id: "AB12CD34",
  buyer: "Покупатель",
  price: "250",
  currency: "RUB",
  login: "steam_player",
  password: "••••••••",
  game: "Counter-Strike 2",
  expires: "20.11.2026 18:30 UTC",
  code: "ABCDE",
  quantity: "100",
  points_per_unit: "100",
  units: "1",
  amount: "143",
  count: "2",
  key: "KEY-1234-5678",
  target: "@example",
  hours: "2",
  minutes: "10",
  photo_timeout: "10",
  attempts_left: "2",
  reject_attempt: "1",
  reason: "нужно более чёткое фото",
  accounts: "Steam • CS2 — до 18:30 UTC",
  url: "https://funpay.com/lots/offer?id=123",
  date: "20.11.2026",
  time: "18:30",
  lease_id: "42",
  label: "Steam #1",
  chat_id: "123",
};

function Editor({ plugin, row }: { plugin: string; row: Template }) {
  const [text, setText] = useState(row.text),
    [saved, setSaved] = useState(row.text),
    [busy, setBusy] = useState(false),
    [preview, setPreview] = useState(false);
  const unknown = [...text.matchAll(/\{([^{}]+)\}/g)]
    .map((m) => m[1])
    .filter((v) => !row.variables.includes(v));
  const auto = useAutoSave({ value: text, dirty: text !== saved, enabled: !!text.trim() && !unknown.length && !busy,
    save: value => request(`/plugins/${plugin}/templates/${row.key}`, "PUT", { text: value }), onSaved: value => setSaved(value) });
  async function save(reset = false) {
    setBusy(true);
    try {
      await request(
        `/plugins/${plugin}/templates/${row.key}`,
        reset ? "DELETE" : "PUT",
        reset ? undefined : { text },
      );
      const value = reset ? row.default : text;
      setText(value);
      setSaved(value);
      toast.success(
        reset ? "Восстановлен текст по умолчанию" : "Шаблон сохранён",
      );
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="template-editor panel">
      <div className="template-title">
        <div>
          <small>
            {row.audience === "admin"
              ? "АДМИНИСТРАТОРУ · TELEGRAM"
              : row.audience === "support"
                ? "ПОДДЕРЖКЕ · FUNPAY"
                : "ПОКУПАТЕЛЮ · FUNPAY"}
          </small>
          <h3>{row.title}</h3>
          <code>{row.key}</code>
        </div>
        <button
          className="template-reset"
          disabled={busy || auto.isSaving}
          title="Сбросить по умолчанию"
          aria-label={`Сбросить ${row.title}`}
          onClick={() => void save(true)}
        >
          <RotateCcw size={17} />
        </button>
      </div>
      {row.condition && <p className="template-condition">{row.condition}</p>}
      <textarea
        aria-label={row.title}
        value={text}
        maxLength={8000}
        rows={6}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
      />
      <div className="template-variables">
        <span>Доступные теги:</span>
        {row.variables.map((v) => (
          <button
            key={v}
            title="Добавить тег в конец текста"
            onClick={() => setText((t) => `${t}{${v}}`)}
          >{`{${v}}`}</button>
        ))}
      </div>
      {!!unknown.length && (
        <p role="alert" className="template-error">
          Недоступные теги: {unknown.join(", ")}
        </p>
      )}
      {preview && (
        <div className="template-preview">
          <small>ПРЕДПРОСМОТР · ПРИМЕР ДАННЫХ</small>
          <p>
            {text.replace(
              /\{([a-z_][a-z0-9_]*)\}/g,
              (_, v: string) => examples[v] || `[${v}]`,
            )}
          </p>
        </div>
      )}
      <footer>
        <Button secondary onClick={() => setPreview(!preview)}>
          <Eye size={16} />
          Предпросмотр
        </Button>
        <span>
          {text.length}/8000{text !== saved ? " · Не сохранено" : ""}
        </span>
        <AutoSaveStatus state={auto} />
      </footer>
    </article>
  );
}

export function MessageTemplates({ plugin }: { plugin: string }) {
  const query = useData<Template[]>(
    `templates-${plugin}`,
    `/plugins/${plugin}/templates`,
  );
  const [search, setSearch] = useState(""),
    [group, setGroup] = useState("Все"),
    [audience, setAudience] = useState("all");
  const rows = query.data || [],
    groups = ["Все", ...new Set(rows.map((r) => r.group))];
  const shown = rows.filter(
    (r) =>
      (group === "Все" || r.group === group) &&
      (audience === "all" || r.audience === audience) &&
      `${r.title} ${r.key} ${r.text}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <div className="message-templates">
      <div className="panel template-intro">
        <MessageSquareText size={28} />
        <div>
          <h3>Ваш голос в каждом сообщении</h3>
          <p>
            Изменения применяются к следующим событиям. Предпросмотр использует
            примеры и ничего не отправляет. Значения тегов подставляются
            автоматически.
          </p>
        </div>
      </div>
      <div className="template-toolbar">
        <label>
          <Search size={17} />
          <input
            placeholder="Поиск по тексту или событию"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <select
          aria-label="Категория шаблонов"
          value={group}
          onChange={(e) => setGroup(e.target.value)}
        >
          {groups.map((g) => (
            <option key={g}>{g}</option>
          ))}
        </select>
        <select
          aria-label="Получатель шаблона"
          value={audience}
          onChange={(e) => setAudience(e.target.value)}
        >
          <option value="all">Все получатели</option>
          <option value="buyer">Покупатель</option>
          <option value="admin">Администратор</option>
          {rows.some((r) => r.audience === "support") && (
            <option value="support">Поддержка FunPay</option>
          )}
        </select>
      </div>
      {query.isLoading ? (
        <p>Загружаем шаблоны…</p>
      ) : query.isError ? (
        <div className="panel">
          <p>Не удалось загрузить шаблоны.</p>
          <Button secondary onClick={() => void query.refetch()}>
            Повторить
          </Button>
        </div>
      ) : (
        <>
          <p className="template-count">
            Показано {shown.length} из {rows.length}
          </p>
          {shown.map((row) => (
            <Editor key={`${plugin}-${row.key}`} plugin={plugin} row={row} />
          ))}
          {!shown.length && (
            <div className="panel">Подходящих шаблонов нет.</div>
          )}
        </>
      )}
    </div>
  );
}
