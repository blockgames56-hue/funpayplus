import { useSyncedSettings } from "./synced-settings";
import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { requestId } from "./request-id";
import { useEffect, useState } from "react";
import {
  Camera,
  Clock3,
  Eye,
  EyeOff,
  FileJson,
  Gamepad2,
  KeyRound,
  MapPin,
  Package,
  Plus,
  Save,
  ShieldCheck,
  Sparkles,
  Timer,
  Users,
  Link2,
  Snowflake,
  RefreshCw,
  Search,
  Trash2,
  AlertTriangle,
} from "lucide-react";
import { toast } from "sonner";
import { avatarBlob, date, request, useData } from "./api";
import { Button, Confirm, Field, Modal, Toggle } from "./ui";
import type { PluginSection } from "./plugin-sections";

type Inventory = {
  id: number;
  label: string;
  login: string;
  game: string;
  notes: string;
  binding_ids: number[];
  state: string;
  last_error: string | null;
};
type Binding = {
  id: number;
  lot_id: number;
  title: string;
  extension: boolean;
  account_id: number;
};
type Lease = {
  id: number;
  account_id: number;
  label: string;
  game: string;
  state: string;
  buyer: string;
  manual: boolean;
  order_number: string | null;
  chat_id: number | null;
  expires_at: string;
  created_at: string;
  finished_at: string | null;
};
type Buyer = {
  account_id: number;
  buyer_id: number;
  buyer: string;
  notes: string;
  blocked: boolean;
  reason: string;
};
type Photo = {
  id: number;
  order_number: string;
  buyer: string;
  state: string;
  photo_state: string;
  rejects: number;
  deadline: string;
  has_image: boolean;
  report?: {
    decision: string;
    checks: { check: string; passed: boolean | null; reason: string }[];
    metadata?: Record<string, unknown>;
  };
};
type Data = {
  stats: Record<string, number | { amount: string; currency: string }[]>;
  inventory: Inventory[];
  bindings: Binding[];
  leases: Lease[];
  chats: { id: number; buyer: string; buyer_id: number; account_id: number }[];
  buyers: Buyer[];
  reviews: Photo[];
  settings: Record<string, unknown>;
};
const ROOT = "/plugins/steam-rent";
const states: Record<string, string> = {
  free: "Свободен",
  leased: "В аренде",
  frozen: "Заморожен",
  quarantine: "Карантин",
  draft: "Нужен maFile",
  rotating: "Меняем пароль",
  verifying: "Проверяем вход",
  reserved: "Ждёт проверки фото",
  active: "Активна",
  finished: "Завершена",
  attention: "Нужна проверка",
};

function Badge({ state }: { state: string }) {
  return (
    <span
      className={`rent-badge ${["free", "active"].includes(state) ? "good" : ["quarantine", "attention"].includes(state) ? "bad" : ""}`}
    >
      {states[state] || state}
    </span>
  );
}
function SwitchField({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="rent-switch-field">
      <div>
        <strong>{label}</strong>
        {hint && <p>{hint}</p>}
      </div>
      <Toggle label={label} checked={checked} onChange={onChange} />
    </div>
  );
}
function Secret({
  value,
  onChange,
  placeholder = "",
}: {
  value: string;
  onChange: (s: string) => void;
  placeholder?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="rent-secret">
      <input
        autoComplete="new-password"
        type={show ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      <button
        type="button"
        aria-label={show ? "Скрыть" : "Показать"}
        onClick={() => setShow(!show)}
      >
        {show ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  );
}

function AccountForm({
  row,
  bindings,
  onClose,
  refresh,
}: {
  row?: Inventory;
  bindings: Binding[];
  onClose: () => void;
  refresh: () => void;
}) {
  const [tab, setTab] = useState("profile"),
    [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    label: row?.label || "",
    game: row?.game || "",
    notes: row?.notes || "",
    binding_ids: row?.binding_ids || [],
    login: row?.login || "",
    password: "",
    mafile: "",
  });
  const set = (key: string, value: unknown) =>
    setForm((f) => ({ ...f, [key]: value }));
  async function save(connection: boolean) {
    setBusy(true);
    try {
      if (row && !connection) {
        const { login: _l, password: _p, mafile: _m, ...metadata } = form;
        await request(`${ROOT}/inventory/${row.id}/metadata`, "PUT", metadata);
      } else {
        await request(
          row ? `${ROOT}/inventory/${row.id}/connection` : `${ROOT}/connect`,
          row ? "PUT" : "POST",
          form,
        );
      }
      toast.success(
        connection && form.mafile
          ? "Вход в Steam подтверждён"
          : "Аккаунт сохранён",
      );
      refresh();
      onClose();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="rental-account-form">
      <div className="rent-tabs">
        {[
          ["profile", "1. Аккаунт"],
          ["security", "2. Доступ Steam"],
          ["bindings", "3. Лоты"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div hidden={tab !== "profile"}>
        <div className="two-fields">
          <Field label="Название аккаунта">
            <input
              value={form.label}
              onChange={(e) => set("label", e.target.value)}
              placeholder="Steam · CS2 #1"
            />
          </Field>
          <Field label="Игра">
            <input
              value={form.game}
              onChange={(e) => set("game", e.target.value)}
              placeholder="Counter-Strike 2"
            />
          </Field>
        </div>
        <Field label="Заметки владельца" hint="Покупателю не отправляются">
          <textarea
            value={form.notes}
            onChange={(e) => set("notes", e.target.value)}
            rows={4}
          />
        </Field>
      </div>
      <div hidden={tab !== "security"}>
        <div className="two-fields">
          <Field label="Логин Steam">
            <input
              autoComplete="off"
              value={form.login}
              onChange={(e) => set("login", e.target.value)}
            />
          </Field>
          <Field label="Пароль Steam">
            <Secret
              value={form.password}
              onChange={(v) => set("password", v)}
            />
          </Field>
        </div>
        <Field
          label="maFile"
          hint="Загрузите JSON-файл мобильного аутентификатора. Логин и SteamID проверяются перед добавлением."
        >
          <label className="rent-upload">
            <FileJson size={22} />
            <span>
              {form.mafile
                ? "maFile загружен · заменить"
                : "Выбрать .maFile / .json"}
            </span>
            <input
              type="file"
              accept=".maFile,.mafile,.json,application/json"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                if (f.size > 100000) {
                  toast.error("maFile больше 100 КБ");
                  return;
                }
                set("mafile", await f.text());
              }}
            />
          </label>
        </Field>
        <Field label="Или вставьте JSON maFile">
          <textarea
            value={form.mafile}
            onChange={(e) => set("mafile", e.target.value)}
            rows={4}
            spellCheck={false}
            className="rent-mafile"
          />
        </Field>
        <p className="rent-note">
          Без maFile аккаунт сохранится как черновик. Автоматическая выдача
          станет доступна после проверки входа. Пароль и секреты хранятся
          зашифрованными.
        </p>
      </div>
      <div hidden={tab !== "bindings"}>
        <p className="rent-note">
          Один Steam-аккаунт можно привязать к нескольким лотам. Одновременно
          его сможет арендовать только один покупатель.
        </p>
        <div className="rent-binding-picks">
          {bindings
            .filter((b) => !b.extension)
            .map((b) => (
              <label key={b.id}>
                <input
                  type="checkbox"
                  checked={form.binding_ids.includes(b.id)}
                  onChange={(e) =>
                    set(
                      "binding_ids",
                      e.target.checked
                        ? [...form.binding_ids, b.id]
                        : form.binding_ids.filter((id) => id !== b.id),
                    )
                  }
                />
                <span>
                  {b.title}
                  <small>
                    Привязка #{b.id} · FunPay #{b.account_id}
                  </small>
                </span>
              </label>
            ))}
        </div>
        {!bindings.some((b) => !b.extension) && (
          <p>Лоты можно привязать позже во вкладке «Товары и правила».</p>
        )}
      </div>
      <footer className="rent-actions">
        <Button secondary onClick={onClose}>
          Отмена
        </Button>
        {row && (
          <Button
            secondary
            disabled={busy || !form.label}
            onClick={() => void save(false)}
          >
            <Save size={16} />
            Сохранить карточку
          </Button>
        )}
        <Button
          disabled={busy || !form.label || !form.login || !form.password}
          onClick={() => void save(true)}
        >
          <ShieldCheck size={16} />
          {busy
            ? "Проверяем Steam…"
            : form.mafile
              ? "Проверить и подключить"
              : row
                ? "Добавьте maFile"
                : "Сохранить черновик"}
        </Button>
      </footer>
    </div>
  );
}

function PhotoImage({ id }: { id: number }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let active = true,
      url = "";
    void avatarBlob(`/api${ROOT}/reviews/${id}/image`)
      .then((blob) => {
        url = URL.createObjectURL(blob);
        if (active) setSrc(url);
        else URL.revokeObjectURL(url);
      })
      .catch(() => {});
    return () => {
      active = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [id]);
  return src ? (
    <a href={src} target="_blank" rel="noreferrer">
      <img
        className="rent-photo"
        src={src}
        alt="Фото рабочего места для проверки"
      />
    </a>
  ) : (
    <p>Загружаем изображение…</p>
  );
}

function useRentSettings(data: Data, _refresh: () => void) {
  const [form, setForm] = useState<Record<string, unknown>>(data.settings),
    [dirty, setDirty] = useState(false),
    [busy] = useState(false);
  useSyncedSettings(data.settings, setForm);
  const set = (key: string, value: unknown) => {
    setForm((f) => ({ ...f, [key]: value }));
    setDirty(true);
  };
  const number = (
    key: string,
    label: string,
    min: number,
    max: number,
    hint?: string,
  ) => (
    <Field label={label} hint={hint}>
      <input
        type="number"
        min={min}
        max={max}
        value={Number(form[key] ?? min)}
        onChange={(e) => set(key, e.target.valueAsNumber)}
      />
    </Field>
  );
  const auto = useAutoSave({ value: form, dirty,
    save: value => request("/plugins/integrated/steam-rent/settings", "PUT", Object.fromEntries(Object.entries(value).filter(([key]) => !key.startsWith("has_")))),
    onSaved: () => setDirty(false), queryKeys: ["rental-workspace", "plugin-catalog"] });
  const save = () => auto.flush();
  const autosave = auto.isSaving;
  const touch = () => setDirty(true);
  return { form, dirty, busy, autosave, set, number, touch, save, auto };
}

function SaveBar({ state }: { state: ReturnType<typeof useAutoSave> }) {
  return <AutoSaveStatus state={state} />;
}

function CategoryHead({
  icon: Icon,
  title,
  text,
}: {
  icon: typeof Camera;
  title: string;
  text: string;
}) {
  return (
    <div className="rent-category-head">
      <Icon size={26} />
      <div>
        <h2>{title}</h2>
        <p>{text}</p>
      </div>
    </div>
  );
}

function RentGeneralSettings({
  data,
  refresh,
}: {
  data: Data;
  refresh: () => void;
}) {
  const { form, set, number, auto } = useRentSettings(data, refresh);
  return (
    <div className="rent-category">
      <CategoryHead
        icon={Timer}
        title="Сроки и уведомления"
        text="Предупреждения об окончании аренды и награда за отзыв. Тексты сообщений редактируются во вкладке «Шаблоны сообщений»."
      />
      <div className="panel rent-form-panel">
        <SwitchField
          label="Предупреждать за 30 минут"
          checked={!!form.warn_30_minutes}
          onChange={(v) => set("warn_30_minutes", v)}
          hint="Первое напоминание приходит покупателю за полчаса до конца аренды."
        />
        <div className="two-fields">
          {number(
            "warn_minutes",
            "Второе предупреждение, мин.",
            0,
            120,
            "0 — отключить. Текст для 10 минут редактируется отдельно.",
          )}
          {number(
            "review_bonus_minutes",
            "Бонус за отзыв 5★, мин.",
            0,
            1440,
            "Один бонус за заказ, добавляется к ближайшей активной аренде.",
          )}
        </div>
      </div>
      <SaveBar state={auto} />
    </div>
  );
}

function RentStockSettings({
  data,
  refresh,
}: {
  data: Data;
  refresh: () => void;
}) {
  const { form, set, auto } = useRentSettings(data, refresh);
  return (
    <div className="rent-category">
      <CategoryHead
        icon={Package}
        title="Лоты и наличие"
        text="Как ведут себя ваши лоты FunPay, когда свободных аккаунтов нет."
      />
      <div className="panel rent-form-panel">
        <SwitchField
          label="Скрывать лоты без свободных аккаунтов"
          checked={!!form.auto_hide_empty}
          onChange={(v) => set("auto_hide_empty", v)}
          hint="Восстанавливаются только лоты, скрытые этим плагином."
        />
        <SwitchField
          label="Поддерживать лоты продления активными"
          checked={!!form.keep_extension_active}
          onChange={(v) => set("keep_extension_active", v)}
          hint="Лот продления остаётся на FunPay, даже когда нет активных аренд."
        />
        <p className="rent-note">
          Привязки лотов к аккаунтам — в разделе «Товары и правила».
        </p>
      </div>
      <SaveBar state={auto} />
    </div>
  );
}

function RentPhotoSettings({
  data,
  refresh,
}: {
  data: Data;
  refresh: () => void;
}) {
  const { form, dirty, set, number, auto } = useRentSettings(
    data,
    refresh,
  );
  const [clubs, setClubs] = useState(
    JSON.stringify(data.settings.photo_clubs || [], null, 2),
  );
  useEffect(() => {
    if (!dirty) {
      setClubs(JSON.stringify(data.settings.photo_clubs || [], null, 2));
    }
  }, [data.settings, dirty]);
  const refs = useData<{ id: string; name: string }[]>(
    "rent-references",
    `${ROOT}/references`,
  );
  return (
    <div className="rent-category">
      <CategoryHead
        icon={Camera}
        title="Проверка ПК-клубов"
        text="Единая проверка для выбранных аккаунтов: фото рабочего места, срок и место съёмки, автоматические проверки. Данные Steam выдаются только после одобрения; продление повторное фото не требует."
      />
      <div className="panel">
        <h3>
          <Camera size={18} />
          Режим и таймеры
        </h3>
        <SwitchField
          label="Требовать фото ПК-клуба"
          checked={!!form.require_photo_review}
          onChange={(v) => set("require_photo_review", v)}
          hint="Очередь снимков и решения — выше, в начале раздела."
        />
        <div className="two-fields">
          <Field label="Режим">
            <select
              value={String(form.photo_mode || "manual")}
              onChange={(e) => set("photo_mode", e.target.value)}
            >
              <option value="manual">Решает продавец</option>
              <option value="automatic">
                Автопроверки + продавец при сомнении
              </option>
            </select>
          </Field>
          {number("photo_timeout_minutes", "Время на отправку фото, мин.", 1, 120)}
          {number("photo_max_rejects", "Максимум отказов продавца", 1, 10)}
          {number(
            "verified_ttl_hours",
            "Помнить проверенного покупателя, ч.",
            0,
            720,
          )}
        </div>
        <Field
          label="Telegram Chat ID для уведомлений"
          hint="Через запятую. Если пусто — используются общие настройки Telegram. Токен бота задаётся в настройках приложения."
        >
          <input
            value={((form.photo_notify_chat_ids as string[]) || []).join(", ")}
            onChange={(e) =>
              set(
                "photo_notify_chat_ids",
                e.target.value
                  .split(",")
                  .map((v) => v.trim())
                  .filter(Boolean),
              )
            }
          />
        </Field>
        <Field
          label="Лоты с проверкой"
          hint="Если ничего не выбрано, проверка действует для всех основных лотов."
        >
          <div className="rent-binding-picks">
            {data.bindings
              .filter((b) => !b.extension)
              .map((b) => (
                <label key={b.id}>
                  <input
                    type="checkbox"
                    checked={((form.photo_lot_ids as number[]) || []).includes(
                      b.lot_id,
                    )}
                    onChange={(e) =>
                      set(
                        "photo_lot_ids",
                        e.target.checked
                          ? [
                              ...((form.photo_lot_ids as number[]) || []),
                              b.lot_id,
                            ]
                          : ((form.photo_lot_ids as number[]) || []).filter(
                              (id) => id !== b.lot_id,
                            ),
                      )
                    }
                  />
                  {b.title}
                </label>
              ))}
          </div>
        </Field>
      </div>
      <div className="panel">
        <h3>
          <MapPin size={18} />
          Дата и геопозиция
        </h3>
        <SwitchField
          label="Проверять дату EXIF"
          checked={!!form.photo_exif_time}
          onChange={(v) => set("photo_exif_time", v)}
          hint="При отсутствии EXIF решение передаётся продавцу. Метаданные могут быть изменены."
        />
        <div className="two-fields">
          {number("photo_time_tolerance", "Допуск времени, мин.", 1, 120)}
          <Field label="Часовой пояс фото">
            <input
              value={String(form.photo_timezone || "Europe/Moscow")}
              onChange={(e) => set("photo_timezone", e.target.value)}
            />
          </Field>
        </div>
        <SwitchField
          label="Проверять GPS из фото"
          checked={!!form.photo_exif_geo}
          onChange={(v) => set("photo_exif_geo", v)}
        />
        {number("photo_geo_radius", "Радиус клуба, м", 20, 10000)}
        <Field
          label="Известные клубы"
          hint={'JSON: [{"name":"Мой клуб","lat":55.75,"lon":37.61}]'}
        >
          <textarea
            value={clubs}
            rows={4}
            onChange={(e) => {
              setClubs(e.target.value);
              try { set("photo_clubs", JSON.parse(e.target.value)); } catch { /* Keep incomplete JSON in the editor. */ }
            }}
          />
        </Field>
        <SwitchField
          label="Искать клубы в OpenStreetMap"
          checked={!!form.photo_overpass}
          onChange={(v) => set("photo_overpass", v)}
          hint="При включении координаты из EXIF отправляются сервису Overpass. Отсутствие клуба в карте не считается доказательством нарушения."
        />
      </div>
      <div className="panel">
        <h3>
          <Sparkles size={18} />
          ИИ и поиск совпадений
        </h3>
        <SwitchField
          label="Искать фото в интернете"
          checked={!!form.photo_reverse}
          onChange={(v) => set("photo_reverse", v)}
          hint="Изображение отправляется Google Cloud Vision. Сбой или неоднозначность переводит заказ продавцу."
        />
        <Field label="API-ключ Google Cloud Vision">
          <Secret
            value={String(form.photo_vision_api_key || "")}
            placeholder={
              form.has_photo_vision_api_key ? "Ключ сохранён" : "Введите ключ"
            }
            onChange={(v) => set("photo_vision_api_key", v)}
          />
        </Field>
        <SwitchField
          label="Анализировать фото через ИИ"
          checked={!!form.photo_ai}
          onChange={(v) => set("photo_ai", v)}
          hint="Фото и референсы отправляются выбранному провайдеру. Автовыдача возможна только при положительном результате всех включённых проверок."
        />
        <div className="two-fields">
          <Field label="Провайдер">
            <select
              value={String(form.photo_ai_provider || "gemini")}
              onChange={(e) => set("photo_ai_provider", e.target.value)}
            >
              {["gemini", "openai", "groq", "openrouter", "custom"].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </Field>
          <Field label="Модель с поддержкой изображений">
            <input
              value={String(form.photo_ai_model || "")}
              onChange={(e) => set("photo_ai_model", e.target.value)}
              placeholder="Название модели вашего провайдера"
            />
          </Field>
        </div>
        <Field label="API-ключ ИИ">
          <Secret
            value={String(form.photo_ai_api_key || "")}
            placeholder={
              form.has_photo_ai_api_key ? "Ключ сохранён" : "Введите ключ"
            }
            onChange={(v) => set("photo_ai_api_key", v)}
          />
        </Field>
        {form.photo_ai_provider === "custom" && (
          <Field label="HTTPS адрес совместимого API">
            <input
              value={String(form.photo_ai_base_url || "")}
              onChange={(e) => set("photo_ai_base_url", e.target.value)}
              placeholder="https://provider.example/v1"
            />
          </Field>
        )}
        <Field label="Инструкция проверки">
          <textarea
            value={String(form.photo_ai_prompt || "")}
            onChange={(e) => set("photo_ai_prompt", e.target.value)}
            rows={4}
          />
        </Field>
        <p className="rent-note">
          Ответ ИИ помогает оценить обстановку. Он не доказывает подлинность
          фото или личность покупателя.
        </p>
      </div>
      <div className="panel">
        <h3>Фото-референсы · {refs.data?.length || 0}/10</h3>
        <p className="rent-note">
          Примеры рабочих мест для модели. Изображения сохраняются
          зашифрованными.
        </p>
        {refs.data?.map((r) => (
          <div className="rent-reference" key={r.id}>
            <span>{r.name}</span>
            <Button
              secondary
              onClick={async () => {
                try {
                  await request(`${ROOT}/references/${r.id}`, "DELETE");
                  await refs.refetch();
                } catch (e) {
                  toast.error((e as Error).message);
                }
              }}
            >
              <Trash2 size={15} />
            </Button>
          </div>
        ))}
        <label className="rent-upload">
          <Plus size={20} />
          Добавить изображение
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              const body = new FormData();
              body.append("file", file);
              try {
                await request(`${ROOT}/references`, "POST", body);
                await refs.refetch();
              } catch (error) {
                toast.error((error as Error).message);
              }
              e.target.value = "";
            }}
          />
        </label>
      </div>
      <SaveBar state={auto} />
    </div>
  );
}
function BuyerCard({ buyer, refresh: _refresh }: { buyer: Buyer; refresh: () => void }) {
  const [notes, setNotes] = useState(buyer.notes),
    [blocked, setBlocked] = useState(buyer.blocked),
    [reason, setReason] = useState(buyer.reason || ""),
    [saved, setSaved] = useState(JSON.stringify({ notes, blocked, reason }));
  const auto = useAutoSave({ value: { notes, blocked, reason }, dirty: JSON.stringify({ notes, blocked, reason }) !== saved,
    save: value => request(`${ROOT}/buyers/${buyer.account_id}/${buyer.buyer_id}`, "PUT", value), onSaved: value => setSaved(JSON.stringify(value)), queryKeys: ["rental-workspace"] });
  return (
    <article className="panel">
      <div className="rent-card-title">
        <h3>{buyer.buyer}</h3>
        <small>FunPay #{buyer.buyer_id}</small>
      </div>
      <Field label="Заметки">
        <textarea
          value={notes}
          rows={3}
          onChange={(e) => setNotes(e.target.value)}
        />
      </Field>
      <SwitchField
        label="Чёрный список"
        checked={blocked}
        onChange={setBlocked}
        hint="Запрещает новые выдачи. Действует во всём приложении."
      />
      {blocked && (
        <Field label="Причина">
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      )}
      <AutoSaveStatus state={auto} />
    </article>
  );
}

export function RentalWorkspace({ section }: { section: PluginSection }) {
  const query = useData<Data>("rental-workspace", `${ROOT}/workspace`);
  const [search, setSearch] = useState(""),
    [edit, setEdit] = useState<Inventory | "new" | null>(null),
    [manual, setManual] = useState(false),
    [showHistory, setShowHistory] = useState(false);
  const [issue, setIssue] = useState({
      account_id: "",
      chat_id: "",
      minutes: 60,
      request_key: requestId(),
    }),
    [extend, setExtend] = useState<Lease | null>(null),
    [minutes, setMinutes] = useState(60),
    [extendKey, setExtendKey] = useState(requestId());
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(
      null,
    ),
    [busy, setBusy] = useState(false),
    [photo, setPhoto] = useState<Photo | null>(null),
    [rejectReason, setRejectReason] = useState(
      "Нужно более чёткое фото рабочего места",
    );
  const data = query.data;
  const refresh = () => {
    void query.refetch();
  };
  useEffect(() => {
    if (!secret) return;
    const timer = setTimeout(() => setSecret(null), 30000);
    return () => clearTimeout(timer);
  }, [secret]);
  async function action(path: string, body?: unknown, method = "POST") {
    setBusy(true);
    try {
      const result = await request<{ message?: string }>(path, method, body);
      toast.success(result.message || "Готово");
      refresh();
      return true;
    } catch (e) {
      toast.error((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  async function guard(row: Inventory) {
    try {
      const r = await request<{ code: string; seconds: number }>(
        `${ROOT}/inventory/${row.id}/guard`,
      );
      setSecret({
        title: `Steam Guard · ${row.login}`,
        value: `${r.code}\nДействует ещё ${r.seconds} сек.`,
      });
    } catch (e) {
      toast.error((e as Error).message);
    }
  }
  if (!data)
    return (
      <div className="panel">
        {query.isError ? (
          <>
            <p>Не удалось загрузить аренду.</p>
            <Button onClick={refresh}>Повторить</Button>
          </>
        ) : (
          "Загружаем аренду…"
        )}
      </div>
    );
  const selected = data.inventory.filter((r) =>
    `${r.label} ${r.game} ${r.login}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <div className="rental-workspace">
      <div className="rent-stats">
        {[
          ["free", "Свободны", Users],
          ["leased", "В аренде", Gamepad2],
          ["today", "Выдано сегодня", Clock3],
          ["quarantine", "Нужна проверка", AlertTriangle],
        ].map(([key, label, Icon]) => {
          const I = Icon as typeof Users;
          return (
            <div key={String(key)}>
              <I size={20} />
              <strong>{String(data.stats[String(key)] || 0)}</strong>
              <span>{String(label)}</span>
            </div>
          );
        })}
      </div>
      {section === "inventory" && (
        <>
          <div className="rent-toolbar">
            <label>
              <Search size={16} />
              <input
                placeholder="Логин, игра или название"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
            <Button onClick={() => setEdit("new")}>
              <Plus size={16} />
              Подключить Steam
            </Button>
          </div>
          <div className="rent-card-grid">
            {selected.map((row) => (
              <article className="panel rent-account-card" key={row.id}>
                <div className="rent-card-title">
                  <div className="rent-game-icon">
                    <Gamepad2 size={24} />
                  </div>
                  <Badge state={row.state} />
                </div>
                <h3>{row.label}</h3>
                <p>
                  {row.game || "Игра не указана"} · {row.login}
                </p>
                <small>
                  <Link2 size={13} /> Лотов: {row.binding_ids.length}
                </small>
                {row.notes && <p className="rent-note">{row.notes}</p>}
                {row.last_error && (
                  <p className="template-error">{row.last_error}</p>
                )}
                <div className="rent-actions">
                  <Button secondary onClick={() => setEdit(row)}>
                    Настроить
                  </Button>
                  <Button
                    secondary
                    disabled={row.state === "draft"}
                    onClick={() => void guard(row)}
                  >
                    <KeyRound size={15} />
                    Guard
                  </Button>
                  {["free", "frozen"].includes(row.state) && (
                    <Button
                      secondary
                      disabled={busy}
                      onClick={() =>
                        void action(
                          `/plugins/integrated/steam-rent/inventory/${row.id}`,
                          { state: row.state === "free" ? "frozen" : "free" },
                          "PATCH",
                        )
                      }
                    >
                      <Snowflake size={15} />
                      {row.state === "free" ? "Заморозить" : "Разморозить"}
                    </Button>
                  )}
                  {row.state === "quarantine" && (
                    <Button
                      secondary
                      disabled={busy}
                      onClick={() =>
                        void action(
                          `/plugins/integrated/steam-rent/inventory/${row.id}/recover`,
                        )
                      }
                    >
                      <RefreshCw size={15} />
                      Проверить новый пароль
                    </Button>
                  )}
                  {["free", "frozen", "draft"].includes(row.state) && (
                    <Confirm
                      title="Удалить аккаунт?"
                      text="Удаление доступно только без истории аренды. Для остальных аккаунтов используйте заморозку."
                      trigger={
                        <Button secondary>
                          <Trash2 size={15} />
                        </Button>
                      }
                      onConfirm={() =>
                        action(
                          `/plugins/integrated/steam-rent/inventory/${row.id}`,
                          undefined,
                          "DELETE",
                        )
                      }
                    />
                  )}
                </div>
              </article>
            ))}
          </div>
          {!selected.length && (
            <div className="panel rent-empty">
              <Gamepad2 size={35} />
              <h3>Подключите первый аккаунт Steam</h3>
              <p>
                Добавьте логин, пароль и maFile, затем выберите лоты для выдачи.
              </p>
            </div>
          )}
        </>
      )}
      {section === "leases" && (
        <>
          <div className="rent-toolbar">
            <label>
              <input
                type="checkbox"
                checked={showHistory}
                onChange={(e) => setShowHistory(e.target.checked)}
              />
              Показать завершённые
            </label>
            <Button
              onClick={() => {
                setIssue({
                  account_id: "",
                  chat_id: "",
                  minutes: 60,
                  request_key: requestId(),
                });
                setManual(true);
              }}
            >
              <Plus size={16} />
              Выдать вручную
            </Button>
          </div>
          <div className="rent-leases">
            {data.leases
              .filter(
                (l) =>
                  showHistory ||
                  ["active", "rotating", "attention"].includes(l.state),
              )
              .map((l) => (
                <article className="panel" key={l.id}>
                  <div className="rent-card-title">
                    <div>
                      <h3>
                        {l.label} <small>#{l.id}</small>
                      </h3>
                      <p>
                        {l.buyer} ·{" "}
                        {l.manual
                          ? "Ручная выдача"
                          : `Заказ #${l.order_number}`}
                      </p>
                    </div>
                    <Badge state={l.state} />
                  </div>
                  <p className="rent-expiry">
                    <Clock3 size={16} />
                    До {date(l.expires_at)}
                  </p>
                  {l.state === "active" && (
                    <div className="rent-actions">
                      <Button
                        secondary
                        onClick={() => {
                          setExtend(l);
                          setMinutes(60);
                          setExtendKey(requestId());
                        }}
                      >
                        Продлить
                      </Button>
                      <Confirm
                        title="Завершить аренду?"
                        text="Приложение сменит пароль и проверит вход. До подтверждения Steam аккаунт останется занят."
                        trigger={<Button secondary>Завершить</Button>}
                        onConfirm={() =>
                          action(
                            `/plugins/integrated/steam-rent/leases/${l.id}/finish`,
                          )
                        }
                      />
                      <Confirm
                        title="Отменить аренду?"
                        text="Доступ покупателя будет закрыт через смену пароля. Возврат денег выполняется отдельно."
                        trigger={<Button secondary>Отменить</Button>}
                        onConfirm={() =>
                          action(`${ROOT}/leases/${l.id}/cancel`)
                        }
                      />
                    </div>
                  )}
                </article>
              ))}
          </div>
          {!data.leases.length && (
            <div className="panel">
              Аренд пока нет. Они появятся после оплаты привязанного лота или
              ручной выдачи.
            </div>
          )}
        </>
      )}
      {section === "tools" && (
        <div className="rent-card-grid">
          {selected.map((row) => (
            <article className="panel" key={row.id}>
              <h3>{row.label}</h3>
              <p className="rent-note">{row.login}</p>
              <Badge state={row.state} />
              <div className="rent-actions">
                <Button secondary onClick={() => void guard(row)}>
                  <KeyRound size={16} />
                  Steam Guard
                </Button>
                <Confirm
                  title="Сменить пароль Steam?"
                  text="Будет создан новый случайный пароль. Аккаунт вернётся в пул только после подтверждённого входа в Steam."
                  trigger={
                    <Button
                      secondary
                      disabled={!["free", "frozen"].includes(row.state)}
                    >
                      Сменить пароль
                    </Button>
                  }
                  onConfirm={() => action(`${ROOT}/inventory/${row.id}/rotate`)}
                />
                <Button
                  secondary
                  onClick={async () => {
                    try {
                      const r = await request<{
                        login: string;
                        password: string;
                      }>(
                        `/plugins/integrated/steam-rent/inventory/${row.id}/credentials`,
                      );
                      setSecret({
                        title: row.label,
                        value: `Логин: ${r.login}\nПароль: ${r.password}`,
                      });
                    } catch (e) {
                      toast.error((e as Error).message);
                    }
                  }}
                >
                  <Eye size={16} />
                  Данные
                </Button>
                <Button
                  secondary
                  disabled={busy || row.state === "draft"}
                  onClick={() =>
                    void action(
                      `/plugins/integrated/steam-rent/inventory/${row.id}/test`,
                    )
                  }
                >
                  Проверить вход
                </Button>
              </div>
            </article>
          ))}
        </div>
      )}
      {section === "customers" && (
        <>
          <div className="rent-toolbar">
            <label>
              <Search size={16} />
              <input
                placeholder="Найти покупателя"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </label>
          </div>
          <div className="rent-card-grid">
            {data.buyers
              .filter((b) =>
                `${b.buyer} ${b.buyer_id}`
                  .toLowerCase()
                  .includes(search.toLowerCase()),
              )
              .map((b) => (
                <BuyerCard
                  key={`${b.account_id}-${b.buyer_id}`}
                  buyer={b}
                  refresh={refresh}
                />
              ))}
          </div>
        </>
      )}
      {section === "verification" && (
        <>
          <div className="panel">
            <h3>Очередь проверки</h3>
            <p className="rent-note">
              Ожидание фото ограничено таймером. После получения снимка решение
              продавца не истекает автоматически.
            </p>
            {data.reviews.map((r) => (
              <div className="rent-review-row" key={r.id}>
                <div>
                  <strong>
                    #{r.order_number} · {r.buyer}
                  </strong>
                  <p>
                    {(
                      {
                        waiting: "Ожидаем фото",
                        processing: "Анализируем",
                        review: "Нужно решение",
                        expired: "Срок фото истёк",
                        rejected: "Отклонено",
                        cancelled: "Отменено",
                        approved: "Одобрено",
                      } as Record<string, string>
                    )[r.photo_state] || r.photo_state}{" "}
                    · Отказов: {r.rejects}
                    {r.photo_state === "waiting"
                      ? ` · До ${date(r.deadline)}`
                      : ""}
                  </p>
                </div>
                <Button
                  secondary
                  onClick={() => {
                    setPhoto(r);
                    setRejectReason("Нужно более чёткое фото рабочего места");
                  }}
                >
                  Открыть
                </Button>
              </div>
            ))}
            {!data.reviews.length && (
              <p className="rent-note">Нет ожидающих проверок.</p>
            )}
          </div>
        </>
      )}
      {section === "rentGeneral" && (
        <RentGeneralSettings data={data} refresh={refresh} />
      )}
      {section === "rentStock" && (
        <RentStockSettings data={data} refresh={refresh} />
      )}
      {section === "verification" && (
        <RentPhotoSettings data={data} refresh={refresh} />
      )}
      <Modal
        open={!!edit}
        onOpenChange={(open) => {
          if (!open) setEdit(null);
        }}
        title={edit === "new" ? "Подключение Steam" : "Настройка аккаунта"}
        description="Доступ, лоты и данные аккаунта находятся в отдельных шагах."
        className="rent-modal"
      >
        {edit && (
          <AccountForm
            row={edit === "new" ? undefined : edit}
            bindings={data.bindings}
            onClose={() => setEdit(null)}
            refresh={refresh}
          />
        )}
      </Modal>
      <Modal
        open={manual}
        onOpenChange={setManual}
        title="Ручная выдача аренды"
        description="Данные будут отправлены в выбранный чат FunPay. Это действие не создаёт оплаченный заказ."
      >
        <Field label="Свободный аккаунт">
          <select
            aria-label="Свободный аккаунт"
            value={issue.account_id}
            onChange={(e) =>
              setIssue((f) => ({ ...f, account_id: e.target.value }))
            }
          >
            <option value="">Выберите аккаунт</option>
            {data.inventory
              .filter((r) => r.state === "free")
              .map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label} · {r.game}
                </option>
              ))}
          </select>
        </Field>
        <Field label="Покупатель и аккаунт FunPay">
          <select
            aria-label="Покупатель и аккаунт FunPay"
            value={issue.chat_id}
            onChange={(e) =>
              setIssue((f) => ({ ...f, chat_id: e.target.value }))
            }
          >
            <option value="">Выберите чат</option>
            {data.chats.map((c) => (
              <option key={c.id} value={c.id}>
                {c.buyer} · FunPay #{c.account_id}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Срок, минут">
          <input
            type="number"
            min={1}
            max={525600}
            value={issue.minutes}
            onChange={(e) =>
              setIssue((f) => ({ ...f, minutes: e.target.valueAsNumber }))
            }
          />
        </Field>
        <Button
          disabled={busy || !issue.account_id || !issue.chat_id}
          onClick={async () => {
            if (
              await action(`${ROOT}/manual-lease`, {
                ...issue,
                account_id: Number(issue.account_id),
                chat_id: Number(issue.chat_id),
              })
            )
              setManual(false);
          }}
        >
          Выдать и отправить данные
        </Button>
      </Modal>
      <Modal
        open={!!extend}
        onOpenChange={(v) => {
          if (!v) setExtend(null);
        }}
        title="Продление аренды"
        description={extend ? `${extend.label} · ${extend.buyer}` : ""}
      >
        <Field label="Добавить минут">
          <input
            type="number"
            min={1}
            max={525600}
            value={minutes}
            onChange={(e) => setMinutes(e.target.valueAsNumber)}
          />
        </Field>
        <Button
          disabled={busy}
          onClick={async () => {
            if (
              extend &&
              (await action(`${ROOT}/leases/${extend.id}/extend`, {
                minutes,
                request_key: extendKey,
              }))
            )
              setExtend(null);
          }}
        >
          Продлить и уведомить
        </Button>
      </Modal>
      <Modal
        open={!!secret}
        onOpenChange={(v) => {
          if (!v) setSecret(null);
        }}
        title={secret?.title || "Доступ Steam"}
        description="Окно очистится автоматически через 30 секунд."
      >
        <pre className="rent-credentials">{secret?.value}</pre>
      </Modal>
      <Modal
        open={!!photo}
        onOpenChange={(v) => {
          if (!v) setPhoto(null);
        }}
        title={photo ? `Проверка #${photo.order_number}` : "Проверка"}
        description={photo?.buyer || ""}
      >
        {photo && (
          <>
            {photo.has_image && <PhotoImage id={photo.id} />}
            <div className="rent-checks">
              {photo.report?.checks?.map((c, i) => (
                <p
                  key={i}
                  className={
                    c.passed === true ? "good" : c.passed === false ? "bad" : ""
                  }
                >
                  {c.passed === true ? "✓" : c.passed === false ? "✕" : "?"}{" "}
                  {c.check}: {c.reason}
                </p>
              ))}
            </div>
            {photo.report?.metadata && (
              <details>
                <summary>Метаданные изображения</summary>
                <pre>{JSON.stringify(photo.report.metadata, null, 2)}</pre>
              </details>
            )}
            {photo.state === "waiting_review" && (
              <>
                <Field label="Причина отказа">
                  <textarea
                    value={rejectReason}
                    onChange={(e) => setRejectReason(e.target.value)}
                    rows={3}
                  />
                </Field>
                <div className="rent-actions">
                  <Button
                    disabled={busy || photo.photo_state !== "review"}
                    onClick={async () => {
                      if (
                        await action(`${ROOT}/reviews/${photo.id}/decision`, {
                          action: "approve",
                        })
                      )
                        setPhoto(null);
                    }}
                  >
                    Одобрить и выдать
                  </Button>
                  <Button
                    secondary
                    disabled={busy || photo.photo_state !== "review"}
                    onClick={async () => {
                      if (
                        await action(`${ROOT}/reviews/${photo.id}/decision`, {
                          action: "reject",
                          reason: rejectReason,
                        })
                      )
                        setPhoto(null);
                    }}
                  >
                    Запросить новое фото
                  </Button>
                  <Button
                    secondary
                    disabled={busy}
                    onClick={async () => {
                      if (
                        await action(`${ROOT}/reviews/${photo.id}/decision`, {
                          action: "cancel",
                        })
                      )
                        setPhoto(null);
                    }}
                  >
                    Отменить проверку
                  </Button>
                </div>
              </>
            )}
          </>
        )}
      </Modal>
    </div>
  );
}
