import { MotionBanner } from "./lottie-mark";
import { useState } from "react";
import { toast } from "sonner";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronRight,
  Clock3,
  KeyRound,
  LifeBuoy,
  Mail,
  Plug,
  Search,
  Settings2,
} from "lucide-react";
import { useAction, useData } from "./api";
import { Button } from "./ui";
import { PluginAvatar } from "./plugin-avatar";

export const pluginDefinitions = [
  {
    id: "kosell-rent",
    name: "KOSell Rent",
    category: "Плагины",
    description: "Аренда через KOSell.store: автовыдача, продление, Steam Guard и автосоздание лотов.",
    icon: Clock3,
  },
  {
    id: "email-code",
    name: "Email Code Fetcher",
    category: "Почта",
    description: "Получение кодов Steam и Epic Games через IMAP IDLE.",
    icon: Mail,
  },
  {
    id: "offline",
    name: "Steam Guard · Коды",
    category: "Плагины",
    description:
      "Ваши Steam-аккаунты с maFile: код 2FA локально, без входа в Steam.",
    icon: KeyRound,
  },
  {
    id: "steam-rent",
    name: "Auto Steam Rent",
    category: "Плагины",
    description:
      "Пул Steam-аккаунтов, Steam Guard, продление и смена пароля по окончании аренды.",
    icon: Clock3,
  },
  {
    id: "support-tickets",
    name: "AutoTicket · Поддержка",
    category: "Поддержка",
    description:
      "Обращения в поддержку FunPay по заказам, которые долго не подтверждаются.",
    icon: LifeBuoy,
  },
];
type PluginState = {
  id: string;
  enabled: boolean;
  configured: boolean;
  available: boolean;
  status: string;
};

export function PluginControl({ id }: { id: string }) {
  const query = useData<PluginState[]>("plugin-catalog", "/plugins/catalog");
  const action = useAction();
  const navigate = useNavigate();
  const state = query.data?.find((row) => row.id === id);
  const enable = () => {
    if (!state) return;
    if (!state.enabled && !state.configured) {
      toast.info(
        "Сначала сохраните настройки плагина" +
          (id === "auto-dump" ? " и включите хотя бы одно правило" : ""),
      );
      navigate(`/plugins/${id}`);
      return;
    }
    action.mutate({
      path: `/plugins/catalog/${id}`,
      method: "PUT",
      body: { is_enabled: !state.enabled },
      success: state.enabled ? "Плагин выключен" : "Плагин включён",
    });
  };
  return (
    <Button
      secondary={state?.enabled}
      disabled={!state || !state.available || action.isPending}
      onClick={enable}
      title={!state?.available ? "Доступна проверка геймпасса" : undefined}
    >
      {state?.enabled ? <Check size={15} /> : <Plug size={15} />}
      {action.isPending
        ? "Применяем…"
        : !state?.available && state
          ? "Только проверка"
          : state?.enabled
            ? "Выключить"
            : "Включить"}
    </Button>
  );
}

const groups = [
  {
    id: "plugins",
    title: "Плагины",
    description: "Steam Guard коды и аренда аккаунтов",
    ids: ["steam-rent", "kosell-rent", "offline"],
  },
  {
    id: "accounts",
    title: "Аккаунты и почта",
    description: "Коды входа с почты",
    ids: ["email-code"],
  },
  {
    id: "communication",
    title: "Общение и поддержка",
    description: "Обращения в поддержку FunPay",
    ids: ["support-tickets"],
  },
];

export function PluginCatalog() {
  const query = useData<PluginState[]>("plugin-catalog", "/plugins/catalog");
  const [search, setSearch] = useState(""),
    [filter, setFilter] = useState("all"),
    [category, setCategory] = useState("all");
  const enabled = query.data?.filter((row) => row.enabled).length ?? 0;
  const visible = pluginDefinitions.filter((plugin) => {
    const state = query.data?.find((row) => row.id === plugin.id);
    return (
      `${plugin.name} ${plugin.description} ${plugin.category}`
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (category === "all" ||
        groups
          .find((group) => group.id === category)
          ?.ids.includes(plugin.id)) &&
      (filter === "all" ||
        (filter === "enabled" ? state?.enabled : !state?.enabled))
    );
  });
  return (
    <div className="plugin-catalog">
      <MotionBanner title="Больше возможностей. Меньше рутины." text="Подключайте плагины, настраивайте автоматизацию и задавайте свой ритм продаж." label="ПЛАГИНЫ · ПРОСТРАНСТВО ВОЗМОЖНОСТЕЙ" />
      <div className="catalog-heading">
        <div>
          <span className="eyebrow">Автоматизация</span>
          <h1>Плагины</h1>
          <p>
            Выберите задачу. Подключение, правила, аккаунты и логи находятся
            внутри модуля.
          </p>
        </div>
        <div className="catalog-count">
          <span>{enabled}</span> включено <i /> {pluginDefinitions.length}{" "}
          модулей
        </div>
      </div>
      <nav className="catalog-category-filter" aria-label="Категории плагинов">
        {[{ id: "all", title: "Все категории" }, ...groups].map((group) => (
          <button
            key={group.id}
            aria-pressed={category === group.id}
            onClick={() => setCategory(group.id)}
          >
            {group.title}
            <span>
              {group.id === "all"
                ? pluginDefinitions.length
                : pluginDefinitions.filter((plugin) =>
                    groups
                      .find((row) => row.id === group.id)
                      ?.ids.includes(plugin.id),
                  ).length}
            </span>
          </button>
        ))}
      </nav>
      <div className="catalog-toolbar">
        <div className="catalog-filters">
          {[
            ["all", "Все плагины"],
            ["enabled", "Включённые"],
            ["disabled", "Выключенные"],
          ].map(([value, label]) => (
            <button
              key={value}
              className={filter === value ? "active" : ""}
              onClick={() => setFilter(value)}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="catalog-search">
          <Search size={16} />
          <input
            aria-label="Поиск плагина"
            placeholder="Название или задача…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
      </div>
      {query.isError && (
        <div className="panel catalog-error">
          Не удалось загрузить состояния плагинов.{" "}
          <Button secondary onClick={() => void query.refetch()}>
            Повторить
          </Button>
        </div>
      )}
      <div className="catalog-groups">
        {groups.map((group) => {
          const plugins = visible.filter((plugin) =>
            group.ids.includes(plugin.id),
          );
          return (
            !!plugins.length && (
              <section
                className={`catalog-group group-${group.id}`}
                key={group.id}
              >
                <div className="catalog-group-heading">
                  <div>
                    <h2>{group.title}</h2>
                    <p>{group.description}</p>
                  </div>
                  <span>{plugins.length}</span>
                </div>
                <div className="catalog-grid">
                  {plugins.map((plugin) => {
                    const state = query.data?.find(
                        (row) => row.id === plugin.id,
                      );
                    return (
                      <article
                        className={`catalog-card ${state?.enabled ? "enabled" : ""}`}
                        key={plugin.id}
                      >
                        <div className="catalog-card-top">
                          <Link
                            to={`/plugins/${plugin.id}`}
                            className="catalog-icon"
                            aria-label={`Настроить ${plugin.name}`}
                          >
                            <PluginAvatar id={plugin.id} name={plugin.name} />
                          </Link>
                          <span
                            className={`catalog-state ${state?.enabled ? "on" : ""}`}
                          >
                            <i />
                            {!state
                              ? "Загрузка…"
                              : !state.available
                                ? "Проверка"
                                : state.enabled
                                  ? "Включён"
                                  : "Выключен"}
                          </span>
                        </div>
                        <span className="catalog-category">
                          {plugin.category}
                        </span>
                        <Link
                          className="catalog-title"
                          to={`/plugins/${plugin.id}`}
                        >
                          {plugin.name}
                          <ArrowUpRight size={16} />
                        </Link>
                        <p>{plugin.description}</p>
                        <span className="catalog-readiness">
                          {state?.configured ? (
                            <>
                              <Check size={12} /> Настройки заполнены
                            </>
                          ) : (
                            <>
                              <Settings2 size={12} /> Требует настройки
                            </>
                          )}
                        </span>
                        <div className="catalog-card-bottom">
                          <PluginControl id={plugin.id} />
                          <Link
                            className="catalog-configure"
                            to={`/plugins/${plugin.id}`}
                          >
                            <Settings2 size={15} /> Настроить{" "}
                            <ChevronRight size={14} />
                          </Link>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </section>
            )
          );
        })}
      </div>
      {!visible.length && (
        <div className="catalog-empty">
          Плагины не найдены.{" "}
          <button
            onClick={() => {
              setSearch("");
              setFilter("all");
              setCategory("all");
            }}
          >
            Сбросить фильтры
          </button>
        </div>
      )}
    </div>
  );
}

export function PluginDetailHeader({ id }: { id: string }) {
  const plugin = pluginDefinitions.find((row) => row.id === id);
  if (!plugin) return null;
  return (
    <>
      <Link to="/plugins" className="plugin-back">
        <ArrowLeft size={16} /> Все плагины
      </Link>
      <div className="plugin-detail-heading">
        <div>
          <span className="eyebrow">{plugin.category}</span>
          <h1 className="plugin-title-with-emoji"><PluginAvatar id={id} name={plugin.name} />{plugin.name}</h1>
          <p>{plugin.description}</p>
        </div>
        <PluginControl id={id} />
      </div>
    </>
  );
}
