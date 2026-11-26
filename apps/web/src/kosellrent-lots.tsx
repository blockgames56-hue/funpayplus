import { useState } from "react";
import { request, useAction } from "./api";
import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { Button, Confirm, Empty, Field, Modal } from "./ui";
import type { State } from "./kosellrent-plugin";

const base = "/plugins/kosell-rent";
type Mapping = State["mappings"][number];
function MappingFields({ state, form, set }: { state: State; form: { product_id: string; hours: string; currency: string }; set: (value: typeof form) => void }) {
  return <div className="kr-form"><Field label="Товар KOSell"><select aria-label="Товар KOSell" value={form.product_id} onChange={e => set({ ...form, product_id: e.target.value })}><option value="">Выберите игру</option>{state.products.map(p => <option key={p.id} value={p.id}>{p.name} · #{p.id}</option>)}</select></Field><Field label="Часов за одну штуку"><input aria-label="Часов за одну штуку" type="number" min={1} max={8760} value={form.hours} onChange={e => set({ ...form, hours: e.target.value })} /></Field><Field label="Валюта закупки"><select aria-label="Валюта закупки" value={form.currency} onChange={e => set({ ...form, currency: e.target.value })}><option value="RUB">RUB</option><option value="USD">USD</option></select></Field></div>;
}

function MappingEditor({ state, mapping, close }: { state: State; mapping: Mapping; close: () => void }) {
  const [form, setForm] = useState({ product_id: String(mapping.product_id), hours: String(mapping.hours), currency: mapping.currency || "RUB" });
  const [dirty, setDirty] = useState(false);
  const auto = useAutoSave({ value: { product_id: Number(form.product_id), hours: Number(form.hours), currency: form.currency }, dirty,
    enabled: !!form.product_id && Number.isInteger(Number(form.hours)) && Number(form.hours) >= 1 && Number(form.hours) <= 8760 && !state.operation.on,
    save: value => request(`${base}/mappings/${mapping.lot_id}`, "PUT", value), onSaved: () => setDirty(false), queryKeys: ["kosell-rent", "plugin-catalog"] });
  return <Modal open onOpenChange={open => { if (!open) close(); }} title={`Изменить привязку · лот #${mapping.lot_id}`} description="Изменения применяются автоматически к этой привязке."><MappingFields state={state} form={form} set={value => { setForm(value); setDirty(true); }} /><AutoSaveStatus state={auto} /><Button secondary onClick={close}>Готово</Button></Modal>;
}

export function KosellLots({ state }: { state: State }) {
  const [form, setForm] = useState({ lot_id: "", product_id: "", hours: "3", currency: "RUB" });
  const [dirty, setDirty] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<Mapping | null>(null);
  const action = useAction();
  const visible = state.mappings.filter(m => `${m.product_name} ${m.lot_id}`.toLowerCase().includes(search.toLowerCase()));
  const validSelection = selected.filter(id => state.mappings.some(m => String(m.lot_id) === id));
  const all = visible.length > 0 && visible.every(m => selected.includes(String(m.lot_id)));
  const auto = useAutoSave({ value: { ...form, lot_id: Number(form.lot_id), product_id: Number(form.product_id), hours: Number(form.hours) }, dirty,
    enabled: !!state.account_id && !!form.lot_id && !!form.product_id && Number.isInteger(Number(form.hours)) && Number(form.hours) >= 1 && Number(form.hours) <= 8760 && !state.operation.on,
    save: value => request(`${base}/mappings`, "POST", value), onSaved: () => setDirty(false), queryKeys: ["kosell-rent", "plugin-catalog"] });
  const remove = async (ids: string[]) => { await action.mutateAsync({ path: `${base}/mappings/delete-selected`, body: { lot_ids: ids.map(Number) }, success: "Выбранные привязки удалены" }); setSelected(old => old.filter(id => !ids.includes(id))); };
  return <div className="kr-stack"><section className="kr-card"><div className="kr-toolbar"><h3>🔗 Привязать лот к KOSell</h3><Button secondary disabled={!state.account_id || action.isPending || !!state.operation.on} onClick={() => action.mutate({ path: `${base}/lots/sync`, success: "Лоты обновлены" })}>Обновить лоты FunPay</Button></div><Field label="Лот FunPay"><select aria-label="Лот FunPay" value={form.lot_id} onChange={e => { setForm(old => ({ ...old, lot_id: e.target.value })); setDirty(true); }}><option value="">Выберите свой лот</option>{state.lots.map(lot => <option key={lot.id} value={lot.id}>#{lot.funpay_lot_id} · {lot.title}</option>)}</select></Field><MappingFields state={state} form={form} set={value => { setForm(old => ({ ...old, ...value })); setDirty(true); }} /><AutoSaveStatus state={auto} /></section>
    <section className="kr-card"><div className="kr-toolbar"><h3>Привязанные лоты · {state.mappings.length}</h3><input aria-label="Поиск лота" placeholder="Игра или ID лота" value={search} onChange={e => setSearch(e.target.value)} /></div>
      {state.mappings.length > 0 && <div className="kr-selection"><label><input type="checkbox" aria-label="Выбрать все лоты" checked={all} onChange={e => setSelected(old => e.target.checked ? [...new Set([...old, ...visible.map(m => String(m.lot_id))])] : old.filter(id => !visible.some(m => String(m.lot_id) === id)))} />{search ? "Выбрать найденные" : "Выбрать все"}</label><small>Выбрано: {validSelection.length}</small><Confirm trigger={<Button secondary disabled={!validSelection.length || action.isPending || !!state.operation.on}>Удалить выбранные привязки</Button>} title={`Удалить привязки: ${validSelection.length}?`} text="Лоты на FunPay и действующие аренды сохранятся. Новые заказы выбранных лотов больше не будут обрабатываться этим плагином." onConfirm={() => remove(validSelection)} /></div>}
      {!state.mappings.length && <Empty title="Пока нет привязок" text="Выберите свой лот и товар выше или создайте лоты во вкладке «Автосоздание»." />}
      <div className="kr-list">{visible.map(m => <article key={m.lot_id}><label className="kr-lot-select"><input type="checkbox" aria-label={`Выбрать лот #${m.lot_id}`} checked={selected.includes(String(m.lot_id))} onChange={e => setSelected(old => e.target.checked ? [...new Set([...old, String(m.lot_id)])] : old.filter(id => id !== String(m.lot_id)))} /></label><div className="kr-lot-caption"><strong>{m.product_name || `Товар #${m.product_id}`}</strong><small>Лот #{m.lot_id} · {m.hours} ч · {m.currency}{state.hidden[String(m.lot_id)] ? " · Скрыт плагином" : ""}</small></div><div className="kr-actions"><Button secondary disabled={!!state.operation.on} onClick={() => setEditing(m)}>Изменить</Button><Button secondary disabled={action.isPending || !!state.operation.on} onClick={() => action.mutate({ path: `${base}/lots/${m.lot_id}/${state.hidden[String(m.lot_id)] ? "restore" : "hide"}`, success: "Состояние лота обновлено" })}>{state.hidden[String(m.lot_id)] ? "Показать" : "Скрыть"}</Button><Confirm trigger={<Button secondary disabled={!!state.operation.on} aria-label={`Удалить привязку ${m.lot_id}`}>Удалить</Button>} title="Удалить привязку?" text="Лот FunPay и действующие аренды сохранятся." onConfirm={() => remove([String(m.lot_id)])} /></div></article>)}</div>
    </section>{editing && <MappingEditor key={editing.lot_id} state={state} mapping={editing} close={() => setEditing(null)} />}</div>;
}
