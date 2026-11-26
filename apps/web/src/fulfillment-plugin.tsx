import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { PluginPart, usePluginSection } from "./plugin-sections";
import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  Gift,
  MessageCircle,
  Search,
  Server,
  ShieldAlert,
  Users,
} from "lucide-react";
import { toast } from "sonner";
import { request, useData } from "./api";
import { Button, Confirm } from "./ui";

type Config = {
  discord_boost: {
    enabled: boolean;
    lot_phrase: string;
    boost_count: number;
    success_message: string;
  };
  steam_gift: {
    enabled: boolean;
    lot_phrase: string;
    api_key: string;
    has_api_key: boolean;
    seller_steamid: string;
    success_message: string;
  };
};
type Task = {
  id: number;
  kind: string;
  status: string;
  target: string | null;
  guild_id: string | null;
  boost_count: number;
  order_number: string;
  buyer_name: string;
  created_at: string;
};
type Data = { settings: Config; tasks: Task[] };

const defaults: Config = {
  discord_boost: {
    enabled: false,
    lot_phrase: "",
    boost_count: 2,
    success_message: "Бусты активированы!",
  },
  steam_gift: {
    enabled: false,
    lot_phrase: "",
    api_key: "",
    has_api_key: false,
    seller_steamid: "",
    success_message: "Подарок отправлен!",
  },
};

export function FulfillmentPlugin({
  kind,
}: {
  kind?: "discord_boost" | "steam_gift";
}) {
  const managed = usePluginSection();
  const query = useData<Data>("fulfillment", "/plugins/fulfillment");
  const [form, setForm] = useState<Config>(defaults);
  const [mode, setMode] = useState<"Friend Code" | "Profile Link">(
    "Friend Code",
  );
  const [targets, setTargets] = useState<Record<number, string>>({});
  const [dirty, setDirty] = useState(false);
  const initialized = useRef(false);
  useEffect(() => {
    if (query.data && !initialized.current) {
      initialized.current = true;
      setForm(query.data.settings);
    }
  }, [query.data]);
  const tasks = (query.data?.tasks ?? []).filter(
    (task) => !kind || task.kind === kind,
  );
  const auto = useAutoSave({ value: form, dirty, enabled: initialized.current,
    save: value => { const { has_api_key: _saved, ...steam } = value.steam_gift;
      return request(kind ? `/plugins/fulfillment/${kind}/settings` : "/plugins/fulfillment", "PUT", kind ? kind === "steam_gift" ? steam : value.discord_boost : { ...value, steam_gift: steam }); },
    onSaved: () => setDirty(false), queryKeys: ["fulfillment", "plugin-catalog"] });
  const update = (value: Config) => { setForm(value); setDirty(true); };
  async function act(
    id: number,
    path: string,
    method = "POST",
    body?: unknown,
  ) {
    try {
      await request(`/plugins/fulfillment/${id}/${path}`, method, body);
      await query.refetch();
      toast.success("Задача обновлена");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Ошибка операции");
    }
  }
  return (
    <section className="fulfillment-plugin">
      <div className="fulfillment-head" hidden={!!managed}>
        <div>
          <span className="eyebrow">Цифровая выдача</span>
          <h2>
            {kind === "discord_boost"
              ? "AutoDiscordBoost"
              : kind === "steam_gift"
                ? "AutoGift · Steam"
                : "AutoDiscordBoost & AutoGift"}
          </h2>
          <p>
            Очередь оплаченных заказов, проверка данных покупателя и ручное
            подтверждение внешней выдачи.
          </p>
        </div>
      </div>
      <div
        className="fulfillment-stats"
        hidden={!!managed && managed !== "jobs"}
      >
        <div>
          <span>Всего задач</span>
          <strong>{tasks.length}</strong>
        </div>
        <div>
          <span>Готовы</span>
          <strong className="green">
            {
              tasks.filter((item) =>
                ["ready", "ready_to_gift"].includes(item.status),
              ).length
            }
          </strong>
        </div>
        <div>
          <span>Требуют внимания</span>
          <strong className="red">
            {
              tasks.filter((item) =>
                ["attention", "uncertain"].includes(item.status),
              ).length
            }
          </strong>
        </div>
      </div>
      <div className="fulfillment-grid">
        <section
          className="panel fulfillment-settings"
          hidden={
            !!managed &&
            !["connection", "settings", "messages"].includes(managed)
          }
        >
          {kind !== "steam_gift" && (
            <>
              <div className="panel-head">
                <h3>
                  <Server size={18} /> AutoDiscordBoost
                </h3>
              </div>
              <p className="muted">
                Приглашение проверяется по Discord API. Бусты подтверждаются
                после выполнения в Discord.
              </p>
              <PluginPart section="settings">
                <label className="field">
                  <span>Фраза в названии лота</span>
                  <input
                    value={form.discord_boost.lot_phrase}
                    onChange={(event) =>
                      update({
                        ...form,
                        discord_boost: {
                          ...form.discord_boost,
                          lot_phrase: event.target.value,
                        },
                      })
                    }
                  />
                </label>
                <label className="field">
                  <span>Кол-во бустов: {form.discord_boost.boost_count}</span>
                  <input
                    type="range"
                    min="1"
                    max="14"
                    value={form.discord_boost.boost_count}
                    onChange={(event) =>
                      update({
                        ...form,
                        discord_boost: {
                          ...form.discord_boost,
                          boost_count: Number(event.target.value),
                        },
                      })
                    }
                  />
                </label>
              </PluginPart>
              <PluginPart section="messages">
                <label className="field">
                  <span>
                    <MessageCircle size={14} /> Сообщение после подтверждения
                  </span>
                  <input
                    value={form.discord_boost.success_message}
                    onChange={(event) =>
                      update({
                        ...form,
                        discord_boost: {
                          ...form.discord_boost,
                          success_message: event.target.value,
                        },
                      })
                    }
                  />
                </label>
              </PluginPart>
            </>
          )}{" "}
          {!kind && <div className="fulfillment-divider" />}
          {kind !== "discord_boost" && (
            <>
              <div className="panel-head">
                <h3>
                  <Gift size={18} /> AutoGift · Steam
                </h3>
              </div>
              <PluginPart section="settings">
                <div className="fulfillment-segment">
                  <button
                    className={mode === "Friend Code" ? "active" : ""}
                    onClick={() => setMode("Friend Code")}
                  >
                    Friend Code
                  </button>
                  <button
                    className={mode === "Profile Link" ? "active" : ""}
                    onClick={() => setMode("Profile Link")}
                  >
                    Profile Link
                  </button>
                </div>
                <p className="muted">
                  {mode === "Friend Code"
                    ? "Код друга будет извлечён из сообщения покупателя. Отправьте заявку в Steam вручную."
                    : "Ссылка /profiles/SteamID64 позволяет проверить принятие заявки через GetFriendList."}
                </p>
              </PluginPart>
              <PluginPart section="settings">
                <label className="field">
                  <span>Фраза в названии лота</span>
                  <input
                    value={form.steam_gift.lot_phrase}
                    onChange={(event) =>
                      update({
                        ...form,
                        steam_gift: {
                          ...form.steam_gift,
                          lot_phrase: event.target.value,
                        },
                      })
                    }
                  />
                </label>
              </PluginPart>
              <PluginPart section="connection">
                <label className="field">
                  <span>SteamID64 продавца</span>
                  <input
                    value={form.steam_gift.seller_steamid}
                    onChange={(event) =>
                      update({
                        ...form,
                        steam_gift: {
                          ...form.steam_gift,
                          seller_steamid: event.target.value,
                        },
                      })
                    }
                  />
                </label>
                <label className="field">
                  <span>Steam Web API Key</span>
                  <input
                    type="password"
                    value={form.steam_gift.api_key}
                    placeholder={
                      form.steam_gift.has_api_key ? "Ключ сохранён" : ""
                    }
                    onChange={(event) =>
                      update({
                        ...form,
                        steam_gift: {
                          ...form.steam_gift,
                          api_key: event.target.value,
                        },
                      })
                    }
                  />
                </label>
              </PluginPart>
              <PluginPart section="messages">
                <label className="field">
                  <span>
                    <MessageCircle size={14} /> Сообщение после подтверждения
                  </span>
                  <input
                    value={form.steam_gift.success_message}
                    onChange={(event) =>
                      update({
                        ...form,
                        steam_gift: {
                          ...form.steam_gift,
                          success_message: event.target.value,
                        },
                      })
                    }
                  />
                </label>
              </PluginPart>
            </>
          )}
          <AutoSaveStatus state={auto} />
        </section>
        <section
          className="panel fulfillment-tasks"
          hidden={!!managed && managed !== "jobs"}
        >
          <div className="panel-head">
            <h3>
              <Users size={18} /> Очередь выдачи
            </h3>
            <span className="tag">Последние 100</span>
          </div>
          {tasks.length ? (
            tasks.map((task) => (
              <div key={task.id} className="fulfillment-task">
                <div className="fulfillment-task-top">
                  <span className="fulfillment-kind">
                    {task.kind === "steam_gift"
                      ? "STEAM GIFT"
                      : "DISCORD BOOST"}
                  </span>
                  <strong>Заказ #{task.order_number}</strong>
                  <small>
                    {task.status === "completed" ? (
                      <CheckCircle2 size={14} />
                    ) : task.status === "attention" ? (
                      <ShieldAlert size={14} />
                    ) : null}
                    {(
                      {
                        waiting_target: "Ожидает данные",
                        ready: "Готов к выдаче",
                        friend_requested: "Ожидает дружбу",
                        ready_to_gift: "Можно отправить подарок",
                        attention: "Нужно внимание",
                        confirming: "Подтверждение",
                        uncertain: "Проверьте выдачу",
                        completed: "Выполнен",
                      } as Record<string, string>
                    )[task.status] || task.status}
                  </small>
                </div>
                <p>
                  {task.buyer_name} ·{" "}
                  {task.target || "Ожидание ссылки или кода покупателя"}
                  {task.guild_id ? ` · сервер ${task.guild_id}` : ""}
                </p>
                <div
                  className="fulfillment-task-actions"
                  hidden={["completed", "confirming", "uncertain"].includes(
                    task.status,
                  )}
                >
                  <div className="fulfillment-target">
                    <Search size={15} />
                    <input
                      aria-label="Данные покупателя"
                      placeholder={
                        task.kind === "steam_gift"
                          ? "Код друга или ссылка Steam"
                          : "Ссылка Discord Invite"
                      }
                      value={targets[task.id] || ""}
                      onChange={(event) =>
                        setTargets({
                          ...targets,
                          [task.id]: event.target.value,
                        })
                      }
                    />
                    <Button
                      secondary
                      disabled={!targets[task.id]}
                      onClick={() =>
                        void act(task.id, "target", "PUT", {
                          value: targets[task.id],
                        })
                      }
                    >
                      Применить
                    </Button>
                  </div>
                  {task.kind === "discord_boost" &&
                    task.target &&
                    !task.guild_id && (
                      <Button
                        secondary
                        onClick={() => void act(task.id, "verify-invite")}
                      >
                        Проверить invite
                      </Button>
                    )}
                  {task.kind === "steam_gift" &&
                    task.target &&
                    ["ready", "attention"].includes(task.status) && (
                      <Button
                        secondary
                        onClick={() => void act(task.id, "friend-requested")}
                      >
                        Заявка отправлена
                      </Button>
                    )}
                  {!["completed", "confirming", "uncertain"].includes(
                    task.status,
                  ) &&
                    (task.kind === "steam_gift"
                      ? [["gift_region_error", "Ошибка региона"]]
                      : [
                          ["discord_invalid_invite", "Неверный invite"],
                          ["discord_no_tokens", "Нет доступных бустов"],
                        ]
                    ).map(([key, label]) => (
                      <Confirm
                        key={key}
                        trigger={<Button secondary>{label}</Button>}
                        title="Сообщить покупателю?"
                        text="В чат FunPay будет отправлен ваш шаблон этой ошибки. Заказ останется для решения продавцом."
                        onConfirm={() => act(task.id, `report/${key}`)}
                      />
                    ))}
                  {["ready", "ready_to_gift", "attention"].includes(
                    task.status,
                  ) && (
                    <Confirm
                      trigger={<Button secondary>Подтвердить выдачу</Button>}
                      title="Внешняя выдача завершена?"
                      text="Нажмите только после фактической отправки подарка или бустов. Покупателю уйдёт сообщение в FunPay."
                      onConfirm={() => act(task.id, "confirm")}
                    />
                  )}
                </div>
                {task.status === "uncertain" && (
                  <Confirm
                    trigger={
                      <Button secondary>Выдача проверена вручную</Button>
                    }
                    title="Товар выдан, чат проверен?"
                    text="Задача будет отмечена выполненной. Повторное сообщение покупателю не отправляется."
                    onConfirm={() => act(task.id, "resolve")}
                  />
                )}
              </div>
            ))
          ) : (
            <div className="fulfillment-empty">
              Задачи появятся после новых оплаченных заказов, подходящих под
              фразу лота.
            </div>
          )}
        </section>
      </div>
    </section>
  );
}
