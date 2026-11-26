import { useState } from "react";
import { Button } from "./ui";
import { toast } from "sonner";
export const EASY_INSTALL = "curl -fsSL https://raw.githubusercontent.com/blockgames56-hue/funpayplus/main/install.sh | bash -s -- --easy";
export function VpsInstallHint() {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(EASY_INSTALL);
      else {
        const field = document.createElement("textarea"); field.value = EASY_INSTALL; field.style.position = "fixed"; field.style.opacity = "0"; document.body.append(field);
        try { field.select(); if (!document.execCommand("copy")) throw new Error("copy"); } finally { field.remove(); }
      }
      setCopied(true); toast.success("Команда скопирована");
    } catch { toast.error("Выделите и скопируйте команду вручную"); }
  }
  return <div className="vps-easy-install"><p>Выполните одну команду в SSH-консоли VPS под root:</p><code>{EASY_INSTALL}</code><Button secondary onClick={() => void copy()}>{copied ? "✓ Скопировано" : "Копировать команду установки"}</Button><p>Домен покупать не нужно. Установщик определит IP, настроит бесплатный HTTPS и спросит токен BotFather. Затем откройте ссылку и нажмите «Запустить» в Telegram.</p><small>Ubuntu/Debian · для Mini App нужны открытые TCP 80/443. Без них работает меню бота. golden_key можно ввести при установке или позже в панели.</small></div>;
}
