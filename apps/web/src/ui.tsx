import {
  useEffect,
  useState,
  type ComponentPropsWithRef,
  type FormEvent,
  type ReactNode,
} from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Switch from "@radix-ui/react-switch";
import { ArrowUpRight, Check, Inbox, LoaderCircle, X } from "lucide-react";
import { toast } from "sonner";
import { avatarSource, cachedAvatar } from "./avatar-cache";
import { AvatarRequestError } from "./api";

export function Button({
  children,
  secondary = false,
  type = "button",
  className = "",
  ...props
}: ComponentPropsWithRef<"button"> & { secondary?: boolean }) {
  return (
    <button
      type={type}
      {...props}
      className={`button ${secondary ? "secondary" : "primary"} ${className}`}
    >
      {children}
    </button>
  );
}
export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <Switch.Root
      className="switch"
      checked={checked}
      onCheckedChange={onChange}
      aria-label={label}
      disabled={disabled}
    >
      <Switch.Thumb className="switch-thumb" />
    </Switch.Root>
  );
}
export function Modal({
  title,
  description,
  trigger,
  children,
  open,
  onOpenChange,
  className = "",
}: {
  title: string;
  description: string;
  trigger?: ReactNode;
  children: ReactNode;
  open?: boolean;
  onOpenChange?: (value: boolean) => void;
  className?: string;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      {trigger && <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>}
      <Dialog.Portal>
        <Dialog.Overlay className="modal-overlay" />
        <Dialog.Content className={`modal ${className}`}>
          <div className="modal-top">
            <div>
              <Dialog.Title>{title}</Dialog.Title>
              <Dialog.Description>{description}</Dialog.Description>
            </div>
            <Dialog.Close aria-label="Закрыть" className="icon-button">
              <X size={18} />
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function Empty({
  title,
  text,
  action,
}: {
  title: string;
  text: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Inbox size={25} />
      </div>
      <h3>{title}</h3>
      <p>{text}</p>
      {action}
    </div>
  );
}
export function Pending() {
  return (
    <div className="loading">
      <LoaderCircle className="spin" size={20} /> Загружаем данные…
    </div>
  );
}
export function Failure({
  error,
  retry,
}: {
  error: Error | null;
  retry: () => void;
}) {
  return (
    <div role="alert" className="error-panel">
      <strong>Не удалось получить данные</strong>
      <p>{error?.message || "Сервис недоступен"}</p>
      <Button secondary onClick={retry}>
        Повторить
      </Button>
    </div>
  );
}
export function Status({ value }: { value: string }) {
  const labels: Record<string, string> = {
    online: "Онлайн",
    offline: "Офлайн",
    error: "Ошибка сессии",
    banned: "Блокировка",
    active: "Доступен",
    dead: "Недоступен",
    checking: "Не проверен",
    paid: "Оплачен",
    closed: "Завершён",
    refunded: "Возврат",
  };
  return (
    <span
      className={`status ${["online", "active", "closed"].includes(value) ? "green" : ["error", "banned", "dead", "refunded"].includes(value) ? "red" : "amber"}`}
    >
      <i />
      {labels[value] || value}
    </span>
  );
}
export function Avatar({
  name,
  url,
  path,
  small = false,
}: {
  name: string;
  url?: string | null;
  path?: string;
  small?: boolean;
}) {
  const [image, setImage] = useState<{ key: string; src: string } | null>(null);
  const safeUrl = avatarSource(url);
  const key = `${path || ""}|${safeUrl || name}`;
  const [retry, setRetry] = useState({ key: "", count: 0 });
  const attempt = retry.key === key ? retry.count : 0;
  useEffect(() => {
    if (!path) return;
    let active = true;
    let objectUrl: string | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    void cachedAvatar(key, path)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setImage({ key, src: objectUrl });
      })
      .catch((error) => {
        if (!active) return;
        setImage(null);
        if (
          !(error instanceof AvatarRequestError && error.status === 404) &&
          attempt < 3
        )
          timer = setTimeout(
            () => setRetry({ key, count: attempt + 1 }),
            [1500, 5000, 15000][attempt],
          );
      });
    return () => {
      active = false;
      clearTimeout(timer);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [key, path, attempt]);
  return (
    <span
      className={`avatar ${small ? "small" : ""}`}
      style={
        {
          "--hue": String(
            [...name].reduce((a, b) => a + b.charCodeAt(0), 0) % 360,
          ),
        } as React.CSSProperties
      }
    >
      {image?.key === key ? (
        <img src={image.src} alt="" onError={() => setImage(null)} />
      ) : (
        [...(name.trim() || "?")][0].toUpperCase()
      )}
    </span>
  );
}
export function External({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="external"
      onClick={(event) => {
        if (window.__TAURI_INTERNALS__) {
          event.preventDefault();
          void import("@tauri-apps/plugin-opener")
            .then(({ openUrl }) => openUrl(href))
            .catch(() => toast.error("Не удалось открыть ссылку"));
        }
      }}
    >
      {children}
      <ArrowUpRight size={13} />
    </a>
  );
}
export function ZoomImage({ src }: { src: string }) {
  return (
    <Modal
      title="Изображение"
      description="Вложение из диалога FunPay"
      trigger={
        <button className="image-zoom-trigger">
          <img src={src} alt="Открыть вложение" loading="lazy" />
        </button>
      }
    >
      <img className="zoom-image" src={src} alt="Вложение в полном размере" />
      <External href={src}>Открыть оригинал</External>
    </Modal>
  );
}
export function Confirm({
  trigger,
  title,
  text,
  onConfirm,
}: {
  trigger: ReactNode;
  title: string;
  text: string;
  onConfirm: () => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      trigger={trigger}
      title={title}
      description={text}
      open={open}
      onOpenChange={setOpen}
    >
      <div className="form-actions">
        <Button secondary onClick={() => setOpen(false)}>
          Отмена
        </Button>
        <Button
          disabled={busy}
          onClick={() => {
            setBusy(true);
            void onConfirm()
              .then(() => setOpen(false))
              .catch(() => undefined)
              .finally(() => setBusy(false));
          }}
        >
          Подтвердить
        </Button>
      </div>
    </Modal>
  );
}
export function Form({
  onSubmit,
  children,
}: {
  onSubmit: (data: FormData) => Promise<unknown>;
  children: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    void onSubmit(data)
      .catch(() => undefined)
      .finally(() => setBusy(false));
  }
  return (
    <form onSubmit={submit}>
      <fieldset disabled={busy}>
        {children}
        <div className="form-actions">
          <Button type="submit" disabled={busy}>
            {busy ? (
              <LoaderCircle className="spin" size={16} />
            ) : (
              <Check size={16} />
            )}{" "}
            Сохранить
          </Button>
        </div>
      </fieldset>
    </form>
  );
}
