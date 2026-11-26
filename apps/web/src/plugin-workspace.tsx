import { RentalWorkspace } from "./rental-workspace";
import { PluginEmoji } from "./plugin-emoji";
import { MessageTemplates } from "./message-templates";
import { type ReactNode, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  CircleHelp,
  Download,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { date, request, useData } from "./api";
import { Button, Confirm, Pending } from "./ui";
import {
  PluginSectionContext,
  sectionGroups,
  sectionInfo,
  sectionsFor,
  type PluginSection,
} from "./plugin-sections";

type State = {
  id: string;
  enabled: boolean;
  configured: boolean;
  available: boolean;
};
type Log = {
  id: number;
  text: string;
  level: string;
  account_id: number | null;
  created_at: string;
};
type LogPage = { items: Log[]; next_cursor: number | null };
const modes: Record<string, string> = {
  "discord-boost":
    "Бусты выполняются в Discord. Здесь ведётся очередь и подтверждается фактическая выдача.",
  "steam-gift":
    "Отправка подарка выполняется в Steam. Плагин проверяет дружбу и ведёт очередь выдачи.",
  "auto-robux":
    "Доступны расчёт комиссии и проверка геймпасса. Автопокупка не поддерживается.",
};

export function PluginLogs({ id }: { id: string }) {
  const [search, setSearch] = useState(""),
    [level, setLevel] = useState(""),
    [cursor, setCursor] = useState<number | null>(null);
  const [previous, setPrevious] = useState<(number | null)[]>([]);
  const [filter, setFilter] = useState("");
  const query = useQuery({
    queryKey: ["plugin-logs", id, level, filter, cursor],
    queryFn: () =>
      request<LogPage>(
        `/plugins/${id}/logs?${new URLSearchParams({ level, q: filter, ...(cursor ? { before: String(cursor) } : {}) })}`,
      ),
    refetchInterval: cursor ? false : 15000,
  });
  function reset() {
    setCursor(null);
    setPrevious([]);
  }
  function download() {
    const rows = query.data?.items || [];
    const blob = new Blob(
      [
        "\ufeffВремя;Уровень;Аккаунт;Событие\n" +
          rows
            .map((row) =>
              [date(row.created_at), row.level, row.account_id ?? "", row.text]
                .map((cell) => '"' + String(cell).replaceAll('"', '""') + '"')
                .join(";"),
            )
            .join("\n"),
      ],
      { type: "text/csv;charset=utf-8" },
    );
    const url = URL.createObjectURL(blob),
      anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${id}-logs.csv`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="panel plugin-log-panel">
      <div className="plugin-log-toolbar">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            reset();
            setFilter(search.trim());
          }}
          className="plugin-log-search"
        >
          <Search size={16} />
          <input
            aria-label="Поиск в логах"
            placeholder="Номер заказа или текст события"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <Button secondary type="submit">
            Найти
          </Button>
        </form>
        <select
          aria-label="Уровень событий"
          value={level}
          onChange={(event) => {
            setLevel(event.target.value);
            reset();
          }}
        >
          <option value="">Все события</option>
          <option value="info">Информация</option>
          <option value="warning">Предупреждения</option>
          <option value="error">Ошибки</option>
        </select>
        <Button
          secondary
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
          title="Обновить логи"
        >
          <RefreshCw size={15} className={query.isFetching ? "spin" : ""} />
        </Button>
        <Button
          secondary
          onClick={download}
          disabled={!query.data?.items.length}
        >
          <Download size={15} /> CSV
        </Button>
        <Confirm
          trigger={<Button secondary>Очистить журнал</Button>}
          title="Очистить логи плагина?"
          text="Будут удалены только записи журнала этого плагина. История заказов, аренд и платежей сохраняется."
          onConfirm={async () => {
            await request(`/plugins/${id}/logs`, "DELETE");
            reset();
            await query.refetch();
          }}
        />
      </div>
      {query.isLoading ? (
        <Pending />
      ) : query.isError ? (
        <div className="plugin-empty">
          <h3>Не удалось загрузить журнал</h3>
          <p>{(query.error as Error).message}</p>
          <Button secondary onClick={() => void query.refetch()}>
            Повторить
          </Button>
        </div>
      ) : query.data?.items.length ? (
        <div
          className={`plugin-log-list ${id === "kosell" ? "kosell-terminal" : ""}`}
          role="log"
          aria-label="События плагина"
        >
          {query.data.items.map((row) => (
            <article
              className={`plugin-log-row level-${row.level}`}
              key={row.id}
            >
              <time>{date(row.created_at)}</time>
              <span className="plugin-log-level">
                {(
                  { info: "Инфо", warning: "Важно", error: "Ошибка" } as Record<
                    string,
                    string
                  >
                )[row.level] || row.level}
              </span>
              <p>{row.text}</p>
              {row.account_id && <small>Аккаунт #{row.account_id}</small>}
            </article>
          ))}
        </div>
      ) : (
        <div className="plugin-empty">
          <FileLogIcon />
          <h3>{filter || level ? "Совпадений нет" : "Пока нет событий"}</h3>
          <p>
            {filter || level
              ? "Измените текст поиска или уровень событий."
              : "Здесь появятся проверки подключения, изменения настроек и результаты работы этого модуля."}
          </p>
        </div>
      )}
      <div className="plugin-log-pagination">
        <span>До 100 событий на странице · время местное</span>
        <div>
          <Button
            secondary
            disabled={!previous.length}
            onClick={() => {
              setCursor(previous.at(-1) ?? null);
              setPrevious((old) => old.slice(0, -1));
            }}
          >
            Новее
          </Button>
          <Button
            secondary
            disabled={!query.data?.next_cursor}
            onClick={() => {
              setPrevious((old) => [...old, cursor]);
              setCursor(query.data?.next_cursor ?? null);
            }}
          >
            Раньше
          </Button>
        </div>
      </div>
    </section>
  );
}
function FileLogIcon() {
  const Icon = sectionInfo.logs.icon;
  return <Icon size={28} />;
}

export function PluginWorkspace({
  id,
  children,
}: {
  id: string;
  children: ReactNode;
}) {
  const [params, setParams] = useSearchParams();
  const choices = sectionsFor(id),
    requested = params.get("section");
  const active: PluginSection = choices.includes(requested as PluginSection)
    ? (requested as PluginSection)
    : "overview";
  const query = useData<State[]>("plugin-catalog", "/plugins/catalog");
  const state = query.data?.find((row) => row.id === id);
  const change = (section: PluginSection) => {
    const next = new URLSearchParams(params);
    next.set("section", section);
    setParams(next);
  };
  const rentalSection =
    id === "steam-rent" &&
    [
      "rentGeneral",
      "rentStock",
      "settings",
      "inventory",
      "leases",
      "verification",
      "customers",
      "tools",
    ].includes(active);
  return (
    <PluginSectionContext.Provider value={active}>
      <div className="plugin-workspace">
        <aside className="plugin-section-nav">
          <span className="plugin-nav-caption">Управление плагином</span>
          <nav aria-label="Разделы плагина">
            {choices.map((key) => {
              return (
                <button
                  key={key}
                  aria-current={active === key ? "page" : undefined}
                  onClick={() => change(key)}
                >
                  <PluginEmoji section={key} />
                  <span>{sectionInfo[key].label}</span>
                  {active === key && <i />}
                </button>
              );
            })}
          </nav>
          <div
            className={`plugin-sidebar-status ${state?.enabled ? "on" : ""}`}
          >
            <i />
            <div>
              <strong>{state?.enabled ? "Работает" : "Выключен"}</strong>
              <span>
                {!state
                  ? "Загрузка состояния"
                  : state.configured
                    ? "Настройки заполнены"
                    : "Нужна настройка"}
              </span>
            </div>
          </div>
        </aside>
        <div className="plugin-section-main">
          <div className="plugin-section-heading">
            <span>
              <PluginEmoji section={active} />
            </span>
            <div>
              <h2>{sectionInfo[active].label}</h2>
              <p>{sectionInfo[active].description}</p>
            </div>
          </div>
          {active === "overview" && (
            <div className="plugin-overview">
              <div className="panel plugin-overview-status">
                <ShieldCheck size={30} />
                <div>
                  <h3>
                    {!state
                      ? "Загружаем состояние…"
                      : !state.available
                        ? "Режим проверки"
                        : state.enabled
                          ? "Плагин включён"
                          : state.configured
                            ? "Можно включать"
                            : "Начните с настройки"}
                  </h3>
                  <p>
                    {modes[id] ||
                      (state?.configured
                        ? "Настройки заполнены. Доступность внешнего сервиса проверяется в разделе подключения."
                        : "Подключите сервис, настройте правила и назначьте товары. После этого включите модуль кнопкой сверху.")}
                  </p>
                </div>
              </div>
              <div className="plugin-overview-grid-wrap">
                {(sectionGroups[id] || [
                  {
                    title: "",
                    ids: choices.filter(
                      (key) => !["overview", "logs"].includes(key),
                    ),
                  },
                ]).map((group) => {
                  const steps = group.ids.filter((key) => choices.includes(key));
                  return (
                    !!steps.length && (
                      <section className="plugin-overview-group" key={group.title}>
                        {!!group.title && <h3>{group.title}</h3>}
                        <div className="plugin-overview-grid">
                          {steps.map((key) => {
                            const ItemIcon = sectionInfo[key].icon;
                            return (
                              <button key={key} onClick={() => change(key)}>
                                <ItemIcon size={22} />
                                <h3>{sectionInfo[key].label}</h3>
                                <p>{sectionInfo[key].description}</p>
                                <ArrowRight size={16} />
                              </button>
                            );
                          })}
                        </div>
                      </section>
                    )
                  );
                })}
              </div>
              <div className="panel plugin-overview-footer">
                <CircleHelp size={20} />
                <p>
                  Проверяйте спорные операции в заказах и журнале. Настройки
                  подключения и тексты сообщений находятся в отдельных разделах.
                </p>
                <Button secondary onClick={() => change("logs")}>
                  Открыть логи <ArrowRight size={15} />
                </Button>
              </div>
            </div>
          )}
          {active === "logs" && <PluginLogs id={id} />}
          {active === "templates" && <MessageTemplates plugin={id} />}
          {rentalSection && <RentalWorkspace key={active} section={active} />}
          <div
            hidden={
              active === "overview" ||
              active === "logs" ||
              active === "templates" ||
              rentalSection
            }
            className="plugin-section-content"
          >
            {children}
          </div>
        </div>
      </div>
    </PluginSectionContext.Provider>
  );
}
