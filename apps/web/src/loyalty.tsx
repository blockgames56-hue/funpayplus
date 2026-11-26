import { useEffect, useState } from 'react';
import { AutoSaveStatus, useAutoSave } from './auto-save';
import { Gift, RefreshCw } from 'lucide-react';
import { useAccountStore } from './store';
import { request, accountQuery, useAction, useData } from './api';
import type { Account, Lot } from './types';
import { Button, Confirm, Field, Toggle } from './ui';

interface Settings {
  enabled: boolean;
  points_per_100_rub: number;
  repeat_bonus: number;
  reward_cost: number;
  reward_text: string;
  promotion_multiplier: number;
  promotion_until: string | null;
}
interface Balance { id: number; account_id: number; buyer_id: number; buyer_name: string; points: number; purchases: number }
interface Reward { id: number; account_id: number; buyer_id: number; points: number; state: string }
interface Promotion { id: number; account_id: number; lot_id: number; original_price: string; discounted_price: string; percent: number; state: string; ends_at: string }

export function LoyaltyPage() {
  const selected = useAccountStore((s) => s.selected);
  const query = useData<Settings>('loyalty-settings', '/settings/loyalty');
  const balances = useData<Balance[]>('loyalty-balances', accountQuery('/loyalty/balances', selected));
  const rewards = useData<Reward[]>('loyalty-rewards', '/loyalty/rewards');
  const promotions = useData<Promotion[]>('promotions', accountQuery('/promotions', selected));
  const lots = useData<Lot[]>('lots', accountQuery('/lots', selected));
  const accounts = useData<Account[]>('accounts', '/accounts');
  const action = useAction();
  const [form, setForm] = useState<Settings | null>(null);
  const [promotionLot, setPromotionLot] = useState('');
  const [discount, setDiscount] = useState(10);
  const [hours, setHours] = useState(24);
  useEffect(() => { if (query.data) setForm(old => old || query.data!); }, [query.data]);
  const auto = useAutoSave({ value: form, dirty: !!form && !!query.data && JSON.stringify(form) !== JSON.stringify(query.data), enabled: !!form,
    save: value => request('/settings/loyalty', 'PUT', value), queryKeys: ['loyalty-settings'] });
  return <>
    <div className="page-head"><div><span className="eyebrow">Покупатели</span><h1>Бонусы и акции</h1><p>Баллы за завершённые покупки и награды постоянным покупателям.</p></div><Gift size={28} /></div>
    {form && <section className="panel loyalty-panel">
      <div className="panel-head"><div><h2>Программа лояльности</h2><p className="muted">Баллы учитываются отдельно для каждого аккаунта. Начисляются за новые завершённые заказы в рублях.</p></div><Toggle label="Включить бонусы" checked={form.enabled} onChange={(enabled) => setForm({ ...form, enabled })} /></div>
      <div className="loyalty-fields">
        <Field label="Баллов за каждые 100 ₽"><input type="number" min={0} max={1000} value={form.points_per_100_rub} onChange={(e) => setForm({ ...form, points_per_100_rub: Number(e.target.value) })} /></Field>
        <Field label="Дополнительные баллы за повторную покупку"><input type="number" min={0} max={10000} value={form.repeat_bonus} onChange={(e) => setForm({ ...form, repeat_bonus: Number(e.target.value) })} /></Field>
        <Field label="Цена награды в баллах"><input type="number" min={1} value={form.reward_cost} onChange={(e) => setForm({ ...form, reward_cost: Number(e.target.value) })} /></Field>
        <Field label="Акция: множитель баллов"><select value={form.promotion_multiplier} onChange={(e) => setForm({ ...form, promotion_multiplier: Number(e.target.value) })}>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>×{n}</option>)}</select></Field>
        <Field label="Акция действует до"><input type="date" value={form.promotion_until || ''} onChange={(e) => setForm({ ...form, promotion_until: e.target.value || null })} /></Field>
        <Field label="Сообщение с наградой" hint="Покупатель получит текст в чате по команде /бонус. {buyer} — имя покупателя."><textarea value={form.reward_text} maxLength={3000} onChange={(e) => setForm({ ...form, reward_text: e.target.value })} /></Field>
      </div>
      <div className="loyalty-help">Покупатель пишет <b>/баллы</b> для проверки остатка и <b>/бонус</b> для получения награды. Если вы обещаете скидку, укажите способ её применения в тексте: цена лота на FunPay автоматически не меняется.</div>
      <AutoSaveStatus state={auto} />
    </section>}
    <section className="panel loyalty-panel"><div className="panel-head"><div><h2>Скидки на лоты</h2><p className="muted">Цена меняется на FunPay и возвращается после окончания акции. Если цена изменена вручную, восстановление остановится для проверки.</p></div></div>
      <div className="promotion-form"><Field label="Лот"><select value={promotionLot} onChange={(e) => setPromotionLot(e.target.value)}><option value="">Выберите лот</option>{lots.data?.map((lot) => <option key={lot.id} value={lot.id}>{lot.title} · {lot.price} ₽</option>)}</select></Field><Field label="Скидка, %"><input type="number" min={1} max={90} value={discount} onChange={(e) => setDiscount(Number(e.target.value))} /></Field><Field label="Срок, часов"><input type="number" min={1} max={720} value={hours} onChange={(e) => setHours(Number(e.target.value))} /></Field><Button disabled={!promotionLot || action.isPending || discount < 1 || discount > 90 || hours < 1 || hours > 720} onClick={async () => { await action.mutateAsync({ path: '/promotions', body: { lot_id: Number(promotionLot), percent: discount, hours }, success: 'Скидка применена' }); void promotions.refetch(); void lots.refetch(); }}>Запустить акцию</Button></div>
      <div className="promotion-list">{promotions.data?.filter((item) => item.state !== 'expired').map((item) => <div className="reward-row" key={item.id}><span><strong>{lots.data?.find((lot) => lot.id === item.lot_id)?.title || `Лот #${item.lot_id}`}</strong><br />{item.original_price} → {item.discounted_price} ₽ · −{item.percent}% · до {new Date(item.ends_at).toLocaleString('ru-RU')} · {item.state === 'active' ? 'Активна' : 'Нужна проверка'}</span>{item.state === 'active' && <Confirm trigger={<Button secondary>Завершить</Button>} title="Завершить акцию?" text="Исходная цена будет восстановлена на FunPay." onConfirm={() => action.mutateAsync({ path: `/promotions/${item.id}/restore`, success: 'Цена восстановлена' })} />}{item.state === 'needs_review' && <Button secondary onClick={() => action.mutate({ path: `/promotions/${item.id}/recover`, success: 'Состояние цены проверено' })}>Проверить цену</Button>}</div>)}</div>
    </section>
    <section className="panel loyalty-panel"><div className="panel-head"><h2>Баллы покупателей</h2><Button secondary onClick={() => { void balances.refetch(); void rewards.refetch(); }}><RefreshCw size={15} /> Обновить</Button></div>
      <div className="table-scroll"><table><thead><tr><th>Покупатель</th><th>Аккаунт</th><th>Покупок</th><th>Баллы</th></tr></thead><tbody>{balances.data?.map((row) => <tr key={row.id}><td>{row.buyer_name} · #{row.buyer_id}</td><td>{accounts.data?.find((a) => a.id === row.account_id)?.username}</td><td>{row.purchases}</td><td><strong>{row.points}</strong></td></tr>)}</tbody></table>{!balances.data?.length && <p className="muted">Начислений пока нет.</p>}</div>
    </section>
    {!!rewards.data?.length && <section className="panel loyalty-panel"><div className="panel-head"><h2>Награды на проверке</h2></div><p>FunPay не подтвердил отправку. Сначала проверьте чат покупателя, затем укажите результат.</p>{rewards.data.map((row) => <div className="reward-row" key={row.id}><span>#{row.buyer_id} · {Math.abs(row.points)} баллов</span><Confirm trigger={<Button secondary>Награда доставлена</Button>} title="Подтвердить доставку?" text="Убедитесь, что сообщение с наградой уже в чате FunPay." onConfirm={() => action.mutateAsync({ path: `/loyalty/rewards/${row.id}/resolve?delivered=true` })} /><Confirm trigger={<Button secondary>Вернуть баллы</Button>} title="Вернуть баллы?" text="Используйте, только если награда не была отправлена." onConfirm={() => action.mutateAsync({ path: `/loyalty/rewards/${row.id}/resolve?delivered=false` })} /></div>)}</section>}
  </>;
}
