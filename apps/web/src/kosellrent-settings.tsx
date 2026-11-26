import { useState } from "react";
import { request } from "./api";
import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { Field, Toggle } from "./ui";
import { PluginEmoji } from "./plugin-emoji";
import type { State } from "./kosellrent-plugin";

type FieldInfo = State["groups"][number]["fields"][number];
export function KosellSettingFields({ fields, values, set, disabled = false }: { fields: FieldInfo[]; values: Record<string, unknown>; set: (key: string, value: unknown) => void; disabled?: boolean }) {
  return <div className="kr-compact-grid">{fields.map(field => field.type === "boolean" ? <div className="kr-toggle" key={field.key}><div><strong>{field.label}</strong>{field.description && <small>{field.description}</small>}</div><Toggle label={field.label} checked={!!values[field.key]} disabled={disabled} onChange={value => set(field.key, value)} /></div> : <Field key={field.key} label={field.label}><div className="kr-setting-input">{field.type === "select" ? <select aria-label={field.label} value={String(values[field.key] ?? "RUB")} disabled={disabled} onChange={e => set(field.key, e.target.value)}><option value="RUB">RUB · Рубли</option><option value="USD">USD · Доллары</option></select> : <input aria-label={field.label} type="number" min={field.min} max={field.max} step="any" value={String(values[field.key] ?? "")} disabled={disabled} onChange={e => set(field.key, e.target.value === "" ? null : Number(e.target.value))} />}{field.description && <details className="kr-field-help"><summary>Подсказка</summary><small>{field.description}</small></details>}</div></Field>)}</div>;
}

export function KosellSettings({ state }: { state: State }) {
  const [category, setCategory] = useState("connection");
  const [account, setAccount] = useState(String(state.account_id || ""));
  const [changes, setChanges] = useState<Record<string, unknown>>({});
  const [apiKey, setApiKey] = useState("");
  const [editingKey, setEditingKey] = useState(false);
  const [dirty, setDirty] = useState(false);
  const values = { ...state.settings, ...changes };
  const set = (key: string, value: unknown) => { setChanges(old => ({ ...old, [key]: value })); setDirty(true); };
  const auto = useAutoSave({
    value: { account_id: Number(account), settings: { ...changes, api_key: apiKey } }, dirty,
    enabled: !!account && !editingKey && !state.operation.on,
    save: value => request("/plugins/kosell-rent/settings", "PUT", value),
    onSaved: () => { setDirty(false); setChanges({}); setApiKey(""); }, queryKeys: ["kosell-rent", "plugin-catalog"],
  });
  const groups = state.groups.filter(group => group.id !== "creation");
  const active = groups.find(group => group.id === category);
  return <section className="kr-card kr-settings-card">
    <div className="kr-toolbar"><h3 className="plugin-title-with-emoji"><PluginEmoji section="settings" />Настройки KOSell Rent</h3><AutoSaveStatus state={auto} /></div>
    <nav className="kr-settings-tabs" aria-label="Категории настроек KOSell"><button type="button" className={category === "connection" ? "active" : ""} onClick={() => setCategory("connection")}>🔑 Подключение</button>{groups.map(group => <button type="button" key={group.id} className={category === group.id ? "active" : ""} onClick={() => setCategory(group.id)}><PluginEmoji section={group.id} />{group.id === "delivery" ? "Выдача" : group.id === "stock" ? "Наличие" : "Продление и отзывы"}</button>)}</nav>
    {category === "connection" && <div className="kr-form"><Field label="Аккаунт FunPay"><select aria-label="Аккаунт FunPay" value={account} disabled={!!state.settings.enabled || !!state.operation.on} onChange={e => { setAccount(e.target.value); setChanges({}); setApiKey(""); setDirty(true); }}><option value="">Выберите аккаунт</option>{state.accounts.map(a => <option key={a.id} value={a.id} disabled={!a.active}>{a.title}{a.active ? "" : " · отключён"}</option>)}</select></Field><Field label="API-ключ KOSell" hint={state.has_api_key ? "Ключ уже сохранён. Новый применяется после выхода из поля." : "Вставьте ключ из личного кабинета KOSell.store."}><input type="password" value={apiKey} autoComplete="new-password" placeholder={state.has_api_key ? "Ключ сохранён" : "Введите API-ключ"} onFocus={() => setEditingKey(true)} onBlur={() => setEditingKey(false)} onChange={e => { setApiKey(e.target.value); setDirty(true); }} /></Field></div>}
    {active && <KosellSettingFields fields={active.fields} values={values} set={set} disabled={Number(account) !== state.account_id || !!state.operation.on} />}
    <small>Параметры создания, цен и витрины собраны во вкладке «Автосоздание».</small>
  </section>;
}
