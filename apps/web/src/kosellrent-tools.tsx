import { useState } from "react";
import { useAction } from "./api";
import { Button, Confirm, Modal } from "./ui";
import { PluginEmoji } from "./plugin-emoji";
import type { State } from "./kosellrent-plugin";

const base = "/plugins/kosell-rent";
const rub = (value: unknown) =>
  new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency: "RUB",
    maximumFractionDigits: 2,
  }).format(Number(value) || 0);
type ProductStats = {
  name: string;
  orders: number;
  rentals: number;
  extensions: number;
  hours: number;
  earned_rub: number;
  spent_rub: number;
};

export function KosellStatistics({ state }: { state: State }) {
  const stats = state.stats;
  const games = Object.entries(
    (stats.by_product || {}) as Record<string, ProductStats>,
  );
  return (
    <section className="kr-card">
      <h3 className="plugin-title-with-emoji">
        <PluginEmoji section="history" />
        Статистика KOSell Rent
      </h3>
      <div className="kr-stat-grid">
        {[
          ["🛒 Оплаченные заказы", Number(stats.orders_total) || 0],
          ["🎮 Выданные аккаунты", Number(stats.rentals_total) || 0],
          ["🔄 Продления", Number(stats.extensions_total) || 0],
          ["🎁 Бонусы за отзывы", Number(stats.review_bonuses_total) || 0],
          ["⏳ Часов выдано", Number(stats.hours_total) || 0],
          ["💰 Выручка FunPay", rub(stats.funpay_earned_rub)],
          ["🛍️ Закупки KOSell", rub(stats.kosell_spent_rub)],
          [
            "📈 Выручка минус закупки",
            rub(
              Number(stats.funpay_earned_rub || 0) -
                Number(stats.kosell_spent_rub || 0),
            ),
          ],
        ].map(([label, value]) => (
          <article key={label}>
            <small>{label}</small>
            <strong>{value}</strong>
          </article>
        ))}
      </div>
      <p>
        Счётчики исходного плагина учитывают обработанные им заказы, закупки,
        продления и бонусные часы.
      </p>
      {games.length > 0 && (
        <div className="kr-table">
          <table>
            <thead>
              <tr>
                <th>Игра</th>
                <th>Заказы</th>
                <th>Аренды / продления</th>
                <th>Часы</th>
                <th>Выручка</th>
                <th>Закупки</th>
              </tr>
            </thead>
            <tbody>
              {games.map(([id, game]) => (
                <tr key={id}>
                  <td>{game.name}</td>
                  <td>{game.orders}</td>
                  <td>
                    {game.rentals} / {game.extensions}
                  </td>
                  <td>{game.hours}</td>
                  <td>{rub(game.earned_rub)}</td>
                  <td>{rub(game.spent_rub)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

type Price = {
  product_id: number;
  game_name: string;
  hours: number;
  cost_rub: number;
  public_rub: number;
  competitor_prices: number[];
};
type Prices = { items: Price[]; total: number; next_offset: number | null };
export function KosellPricePreview({
  state,
  dirty,
}: {
  state: State;
  dirty: boolean;
}) {
  const [data, setData] = useState<Prices | null>(null);
  const [offset, setOffset] = useState(0);
  const action = useAction();
  const load = (next: number) =>
    action.mutate(
      {
        path: `${base}/prices`,
        body: { offset: next, limit: 6, competitors: true },
      },
      {
        onSuccess: (value) => {
          setData(value as Prices);
          setOffset(next);
        },
      },
    );
  return (
    <section className="kr-card">
      <div className="kr-toolbar">
        <h3 className="plugin-title-with-emoji">
          <PluginEmoji section="catalog" />
          Предпросмотр цен и конкурентов
        </h3>
        <Button
          secondary
          disabled={
            !state.plan.length ||
            dirty ||
            action.isPending ||
            state.operation.on
          }
          onClick={() => load(0)}
        >
          {action.isPending ? "Считаем цены…" : "Рассчитать цены"}
        </Button>
      </div>
      <p>
        Себестоимость KOSell, цена покупателю по вашему шаблону и пять самых
        низких подходящих цен FunPay. Сначала подготовьте план создания.
      </p>
      {data && (
        <>
          <div className="kr-table">
            <table>
              <thead>
                <tr>
                  <th>Игра</th>
                  <th>Срок</th>
                  <th>KOSell</th>
                  <th>Ваша цена</th>
                  <th>Конкуренты FunPay</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((price, i) => (
                  <tr key={i}>
                    <td>{price.game_name}</td>
                    <td>{price.hours} ч</td>
                    <td>{rub(price.cost_rub)}</td>
                    <td>{rub(price.public_rub)}</td>
                    <td>
                      {price.competitor_prices.length
                        ? price.competitor_prices.map((p) => rub(p)).join(" / ")
                        : "Нет подходящих предложений"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="kr-toolbar">
            <small>
              Позиции {offset + 1}–{offset + data.items.length} из {data.total}
            </small>
            <div className="kr-actions">
              <Button
                secondary
                disabled={offset === 0 || action.isPending}
                onClick={() => load(Math.max(0, offset - 6))}
              >
                Назад
              </Button>
              <Button
                secondary
                disabled={data.next_offset === null || action.isPending}
                onClick={() => load(data.next_offset!)}
              >
                Далее
              </Button>
            </div>
          </div>
        </>
      )}
    </section>
  );
}

export function KosellMapTools({ state }: { state: State }) {
  const action = useAction();
  const [result, setResult] = useState("");
  const [open, setOpen] = useState(false);
  return (
    <section className="kr-card">
      <h3 className="plugin-title-with-emoji">
        <PluginEmoji section="tools" />
        Карта игр и обслуживание
      </h3>
      <p>
        Карта связывает игры KOSell с разделами FunPay. Диагностика объяснит
        пропуски и лимиты; обновление сбросит связанные кэши.
      </p>
      <div className="kr-actions">
        <Button
          secondary
          disabled={
            !state.has_api_key || action.isPending || state.operation.on
          }
          onClick={() =>
            action.mutate(
              { path: `${base}/diagnostics` },
              {
                onSuccess: (value) => {
                  setResult((value as { text: string }).text);
                  setOpen(true);
                },
              },
            )
          }
        >
          🧭 Диагностика карты
        </Button>
        <Button
          secondary
          disabled={
            !state.has_api_key || action.isPending || state.operation.on
          }
          onClick={() =>
            action.mutate(
              { path: `${base}/refresh-map` },
              {
                onSuccess: (value) => {
                  const r = value as {
                    games: number;
                    products: number;
                    cached: boolean;
                  };
                  setResult(
                    `Игр в карте: ${r.games}\nТоваров в каталоге: ${r.products}\n${r.cached ? "Свежая карта недоступна — используется сохранённая копия." : "Карта загружена с сервера KOSell."}\nПлан создания сброшен. Подготовьте его заново.`,
                  );
                  setOpen(true);
                },
              },
            )
          }
        >
          🔄 Обновить карту и каталог
        </Button>
        <Confirm
          trigger={
            <Button
              secondary
              disabled={
                !state.mappings.length || action.isPending || state.operation.on
              }
            >
              🧹 Очистить все привязки
            </Button>
          }
          title="Очистить привязки плагина?"
          text="Лоты на FunPay и действующие аренды сохранятся. Все связи товаров и отметки скрытия будут очищены; лоты, скрытые ранее, автоматически не включатся."
          onConfirm={() =>
            action.mutateAsync({
              path: `${base}/mappings`,
              method: "DELETE",
              success: "Привязки очищены, лоты сохранены",
            })
          }
        />
      </div>
      <Modal
        title="🧭 Карта KOSell Rent"
        description="Результат проверки исходным плагином"
        open={open}
        onOpenChange={setOpen}
      >
        <div className="kr-help">{result}</div>
      </Modal>
    </section>
  );
}
