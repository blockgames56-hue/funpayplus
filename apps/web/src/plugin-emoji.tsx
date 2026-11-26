import "./plugin-emoji.css";

const sectionEmoji: Record<string, string> = {
  overview: "✨",
  connection: "🔑",
  settings: "⚙️",
  messages: "💬",
  templates: "📝",
  inventory: "🎮",
  leases: "⏳",
  bindings: "🔗",
  logs: "📜",
  history: "📊",
  tools: "🛠️",
  verification: "🛡️",
  customers: "👥",
  rentGeneral: "🔔",
  rentStock: "📦",
  requests: "🎫",
  jobs: "🛒",
  catalog: "🛍️",
  studio: "🎨",
  suppliers: "🤝",
  wallet: "💎",
  delivery: "🛡️",
  stock: "📦",
  buyers: "🎁",
  creation: "🏗️",
  lots: "🔗",
  rentals: "⏳",
  events: "📜",
};
const pluginEmoji: Record<string, string> = {
  "kosell-rent": "🦊",
  kosell: "🦊",
  "steam-rent": "🎮",
  offline: "🛡️",
  "email-code": "📬",
  "support-tickets": "🎫",
  lzt: "🛍️",
  "auto-smm": "🚀",
  "steam-points": "⭐",
  "auto-stars": "🌟",
  "auto-dump": "📉",
  "auto-robux": "💎",
  "discord-boost": "⚡",
  "steam-gift": "🎁",
  "app-route": "🔀",
};

export function PluginEmoji({
  section,
  plugin,
  large = false,
}: {
  section?: string;
  plugin?: string;
  large?: boolean;
}) {
  return (
    <span aria-hidden="true" className={`plugin-emoji${large ? " large" : ""}`}>
      {(plugin && pluginEmoji[plugin]) ||
        (section && sectionEmoji[section]) ||
        "✨"}
    </span>
  );
}
