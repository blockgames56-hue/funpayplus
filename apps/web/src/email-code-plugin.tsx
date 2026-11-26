import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { usePluginSection } from "./plugin-sections";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Eye, EyeOff, Mail, PlugZap } from "lucide-react";
import { toast } from "sonner";
import { request, useData } from "./api";
import { Button, Modal } from "./ui";

type Status = {
  configured: boolean;
  connected: boolean;
  host: string;
  port: number;
  username: string;
  has_password: boolean;
};
type Form = { host: string; port: number; username: string; password: string };

function FloatingField({
  label,
  value,
  onChange,
  type = "text",
  action,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  action?: React.ReactNode;
}) {
  return (
    <label className={`email-floating ${value ? "filled" : ""}`}>
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder=" "
        required={label !== "App Password"}
      />
      <span>{label}</span>
      {action}
    </label>
  );
}

export function EmailCodePlugin({ embedded = false }: { embedded?: boolean }) {
  const managed = usePluginSection();
  const queryClient = useQueryClient();
  const status = useData<Status>("email-code-status", "/plugins/email-code");
  const [open, setOpen] = useState(false);
  const initialized = useRef(false);
  const [form, setForm] = useState<Form>({
    host: "",
    port: 993,
    username: "",
    password: "",
  });
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<{ type: string; text: string }[]>([]);
  useEffect(() => {
    if (status.data && !initialized.current) {
      initialized.current = true;
      setForm({
        host: status.data.host,
        port: status.data.port,
        username: status.data.username,
        password: "",
      });
    }
  }, [status.data]);
  const [dirty, setDirty] = useState(false), [editing, setEditing] = useState(false);
  const update = (value: Form) => { setForm(value); setDirty(true); };
  const auto = useAutoSave({ value: form, dirty, enabled: !editing && !!form.host && !!form.username && !!(form.password || status.data?.has_password),
    save: value => request("/plugins/email-code", "PUT", value), onSaved: () => setDirty(false), queryKeys: ["email-code-status", "plugin-catalog"] });
  const addLog = (type: string, text: string) =>
    setLogs((rows) => [...rows.slice(-29), { type, text }]);
  async function run(test: boolean) {
    setBusy(true);
    addLog("info", `Connecting to ${form.host}:${form.port}...`);
    try {
      await request(
        test ? "/plugins/email-code/test" : "/plugins/email-code",
        test ? "POST" : "PUT",
        form,
      );
      addLog(
        "success",
        test ? "Подключение подтверждено." : "Настройки сохранены.",
      );
      if (!test) {
        void queryClient.invalidateQueries({ queryKey: ["plugin-catalog"] });
        toast.success("Почта сохранена");
        setForm((old) => ({ ...old, password: "" }));
        void status.refetch();
      }
    } catch (error) {
      addLog(
        "error",
        error instanceof Error ? error.message : "Не удалось подключиться.",
      );
    } finally {
      setBusy(false);
    }
  }
  const content = (
    <div className="email-code-layout">
      <form
        className="email-code-form"
        onFocus={() => setEditing(true)} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setEditing(false); }}
        onSubmit={(event) => {
          event.preventDefault();

        }}
      >
        <FloatingField
          label="IMAP Сервер"
          value={form.host}
          onChange={(host) => update({ ...form, host })}
        />
        <FloatingField
          label="Порт"
          type="number"
          value={String(form.port)}
          onChange={(port) => update({ ...form, port: Number(port) })}
        />
        <FloatingField
          label="Email"
          type="email"
          value={form.username}
          onChange={(username) => update({ ...form, username })}
        />
        <FloatingField
          label="App Password"
          type={showPassword ? "text" : "password"}
          value={form.password}
          onChange={(password) => update({ ...form, password })}
          action={
            <button
              type="button"
              aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}
              onClick={() => setShowPassword(!showPassword)}
            >
              {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          }
        />
        {status.data?.has_password && (
          <small>Оставьте пароль пустым, чтобы использовать сохранённый.</small>
        )}
        <div className="email-code-actions">
          <Button
            type="button"
            secondary
            disabled={busy || !form.host || !form.username}
            onClick={() => void run(true)}
          >
            Проверить
          </Button>
          <AutoSaveStatus state={auto} />
        </div>
        {managed && logs.length > 0 && (
          <p
            className={logs.at(-1)?.type === "error" ? "inline-error" : "muted"}
          >
            {logs.at(-1)?.text}
          </p>
        )}
      </form>
      <div
        className="email-code-terminal"
        hidden={!!managed}
        role="log"
        aria-label="Лог IMAP соединения"
      >
        <span className="terminal-title">IMAP TERMINAL</span>
        {logs.length ? (
          logs.map((row, index) => (
            <div key={index} className={`terminal-${row.type}`}>
              &gt; [{row.type.toUpperCase()}] {row.text}
            </div>
          ))
        ) : (
          <div className="terminal-muted">
            &gt; Ожидание проверки соединения…
          </div>
        )}
      </div>
    </div>
  );
  if (embedded)
    return <section className="panel email-settings-panel">{content}</section>;
  return (
    <>
      <button className="email-plugin-card" onClick={() => setOpen(true)}>
        <span className="email-plugin-icon">
          <Mail size={22} />
        </span>
        <span className="email-plugin-copy">
          <strong>Email Code Fetcher</strong>
          <small>Steam и Epic Games · IMAP IDLE</small>
        </span>
        <span
          className={`email-plugin-state ${status.data?.connected ? "connected" : ""}`}
        >
          <i />
          {status.data?.connected
            ? "IMAP Connected"
            : status.data?.configured
              ? "Переподключение"
              : "Не настроен"}
        </span>
        <PlugZap size={19} />
      </button>
      <Modal
        open={open}
        onOpenChange={setOpen}
        title="Email Code Fetcher"
        description="Подключите почтовый ящик для получения кодов через IMAP IDLE."
      >
        {content}
      </Modal>
    </>
  );
}
