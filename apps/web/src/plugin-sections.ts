import {
  createContext,
  createElement,
  useContext,
  type ReactNode,
} from "react";
import {
  Activity,
  BookOpen,
  Clock3,
  Coins,
  FileText,
  History,
  KeyRound,
  LayoutDashboard,
  Link2,
  MessageSquare,
  Package,
  Settings2,
  Timer,
  Users,
} from "lucide-react";

export const sectionInfo = {
  catalog: {
    label: "Подключение и каталог",
    description: "API-ключ KoSell, баланс, товары и остатки.",
    icon: BookOpen,
  },
  studio: {
    label: "Студия привязок",
    description: "Ваши лоты FunPay, товары KoSell и длительность аренды.",
    icon: Link2,
  },
  templates: {
    label: "Шаблоны сообщений",
    description: "Все ответы, ошибки и уведомления с переменными.",
    icon: MessageSquare,
  },
  verification: {
    label: "Проверка ПК-клубов",
    description: "Фото, ручные решения и автоматические проверки.",
    icon: Users,
  },
  customers: {
    label: "Покупатели",
    description: "История аренды, заметки и ограничения.",
    icon: Users,
  },
  tools: {
    label: "Инструменты Steam",
    description: "Steam Guard и проверенная смена пароля.",
    icon: KeyRound,
  },
  overview: {
    label: "Обзор",
    description: "Состояние модуля и порядок запуска.",
    icon: LayoutDashboard,
  },
  connection: {
    label: "Подключение",
    description: "Учётные данные и проверка доступа к сервису.",
    icon: KeyRound,
  },
  settings: {
    label: "Настройки",
    description: "Правила работы, ограничения и поведение плагина.",
    icon: Settings2,
  },
  messages: {
    label: "Сообщения",
    description: "Тексты для покупателя и доступные переменные.",
    icon: MessageSquare,
  },
  bindings: {
    label: "Товары и правила",
    description: "Лоты, услуги и количество за одну оплаченную единицу.",
    icon: Link2,
  },
  inventory: {
    label: "Аккаунты Steam",
    description: "Пул аккаунтов, проверка доступа и восстановление.",
    icon: Users,
  },
  leases: {
    label: "Аренды",
    description: "Активные аренды, сроки и завершение доступа.",
    icon: Clock3,
  },
  jobs: {
    label: "Заказы",
    description: "Очередь, получатели и действия по каждому заказу.",
    icon: Package,
  },
  requests: {
    label: "Создать обращение",
    description: "Выбор подходящих заказов и отправка в поддержку.",
    icon: FileText,
  },
  history: {
    label: "История",
    description: "Результаты работы и завершённые операции.",
    icon: History,
  },
  suppliers: {
    label: "Поставщики",
    description: "Сервисы поставщиков, ключи и состояние подключения.",
    icon: BookOpen,
  },
  wallet: {
    label: "Кошелёк TON",
    description: "Кошелёк оплаты и ключ доступа к TON API.",
    icon: Coins,
  },
  logs: {
    label: "Логи",
    description: "Только события этого плагина: поиск, ошибки и выгрузка.",
    icon: Activity,
  },
  rentGeneral: {
    label: "Сроки и уведомления",
    description: "Предупреждения об окончании аренды и бонус за отзыв 5★.",
    icon: Timer,
  },
  rentStock: {
    label: "Лоты и наличие",
    description: "Скрытие лотов без свободных аккаунтов и лот продления.",
    icon: Package,
  },
};
export type PluginSection = keyof typeof sectionInfo;
const sections: Record<string, PluginSection[]> = {
  kosell: ["catalog", "studio", "leases"],
  "auto-smm": ["connection", "settings", "bindings", "messages", "jobs"],
  "steam-points": ["connection", "settings", "bindings", "messages", "jobs"],
  "steam-rent": [
    "rentGeneral",
    "rentStock",
    "inventory",
    "verification",
    "customers",
    "tools",
    "bindings",
    "leases",
    "messages",
    "jobs",
  ],
  "auto-stars": [
    "connection",
    "wallet",
    "settings",
    "bindings",
    "messages",
    "jobs",
  ],
  "support-tickets": [
    "connection",
    "settings",
    "messages",
    "requests",
    "history",
  ],
  "email-code": ["connection"],
  lzt: ["connection", "settings", "history"],
  "discord-boost": ["settings", "messages", "jobs"],
  "steam-gift": ["connection", "settings", "messages", "jobs"],
  "app-route": ["suppliers", "bindings", "history"],
  offline: ["inventory"],
  "auto-dump": ["bindings"],
  "auto-robux": ["connection", "settings"],
};
export const sectionGroups: Record<string, { title: string; ids: PluginSection[] }[]> = {
  "steam-rent": [
    {
      title: "Настройки аренды",
      ids: ["rentGeneral", "rentStock"],
    },
    {
      title: "Проверка ПК-клубов",
      ids: ["verification"],
    },
    {
      title: "Пул аккаунтов и аренды",
      ids: ["inventory", "customers", "tools", "leases"],
    },
    {
      title: "Заказы и сообщения",
      ids: ["bindings", "messages", "jobs"],
    },
  ],
};
export const sectionsFor = (id: string): PluginSection[] => [
  "overview",
  ...(sections[id] || ["settings"]).filter(
    (key) =>
      key !== "messages" ||
      ["responder", "auto-ticket", "reviews"].includes(id),
  ),
  // Коды Guard хранятся локально; шаблоны сообщений этому плагину не нужны.
  ...(id === "offline" ? [] : (["templates"] as PluginSection[])),
  "logs",
];
export const PluginSectionContext = createContext<PluginSection | null>(null);
export const usePluginSection = () => useContext(PluginSectionContext);
export function PluginPart({
  section,
  children,
}: {
  section: PluginSection;
  children: ReactNode;
}) {
  const active = usePluginSection();
  return createElement(
    "div",
    { className: "plugin-form-part", hidden: !!active && active !== section },
    children,
  );
}
