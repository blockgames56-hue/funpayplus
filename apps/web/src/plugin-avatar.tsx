import { useState } from "react";
import { PluginEmoji } from "./plugin-emoji";
import "./plugin-avatar.css";

// Vite picks up matching files automatically; no imports or registry edits needed.
const logos = import.meta.glob<string>(
  ["./assets/plugin-logos/*.png", "./assets/plugin-logos/*.PNG"],
  { eager: true, query: "?url", import: "default" },
);

export function PluginAvatar({ id, name }: { id: string; name: string }) {
  const src = logos[`./assets/plugin-logos/${id}.png`] || logos[`./assets/plugin-logos/${id}.PNG`];
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  return (
    <span className="plugin-avatar" data-plugin-avatar={id} aria-hidden="true" title={`Логотип ${name}`}>
      {src && src !== failedSrc ? (
        <img src={src} alt="" decoding="async" onError={() => setFailedSrc(src)} />
      ) : (
        <PluginEmoji plugin={id} />
      )}
    </span>
  );
}
