import { useEffect } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { LayoutDashboard, Inbox, Package, Plug, Settings, ArrowUpRight } from "lucide-react";
import { money, useData } from "./api";
import type { Dashboard } from "./types";
import { telegramApp } from "./telegram";

const links = [["/telegram", "Обзор", LayoutDashboard], ["/inbox", "Чаты", Inbox], ["/lots", "Лоты", Package], ["/plugins", "Плагины", Plug], ["/settings", "Настройки", Settings]] as const;
export function TelegramNavigation() {
  const location = useLocation(), navigate = useNavigate();
  useEffect(() => {
    let disposed = false;
    const back = () => navigate("/telegram");
    void telegramApp().then(app => { if (disposed) return; if (location.pathname === "/telegram") app.BackButton.hide(); else app.BackButton.show(); app.BackButton.onClick(back); });
    return () => { disposed = true; window.Telegram?.WebApp?.BackButton.offClick(back); };
  }, [location.pathname, navigate]);
  return <nav className="tg-bottom-nav" aria-label="Telegram навигация">{links.map(([path, label, Icon]) => <NavLink key={path} to={path}><Icon size={20}/><span>{label}</span></NavLink>)}</nav>;
}
export function TelegramHome() {
  const query = useData<Dashboard>("dashboard", "/dashboard");
  const sections = [["/accounts", "👤", "Аккаунты", "Состояние и автоматизация"], ["/inbox", "💬", "Переписка", "Сообщения покупателей"], ["/lots", "📦", "Лоты", "Редактирование и действия"], ["/plugins", "🧩", "Плагины", "KOSell Rent и остальные"], ["/auto-replies", "⚡", "Автоответы", "Правила и шаблоны"], ["/loyalty", "🎁", "Бонусы", "Отзывы и акции"], ["/auto-delivery", "🔑", "Автовыдача", "Правила и товары"], ["/financials", "📈", "Финансы", "Заказы и статистика"], ["/blacklist", "🛡️", "Чёрный список", "Управление покупателями"], ["/proxies", "🌐", "Прокси", "Подключения аккаунтов"], ["/logs", "📋", "Журнал", "События и диагностика"], ["/settings", "⚙️", "Настройки", "Параметры приложения"]];
  return <div className="tg-home"><div className="tg-welcome"><span className="eyebrow">FUNPAY PLUS · TELEGRAM</span><h1>Магазин под рукой</h1><p>Одно пространство с вашим приложением. Изменения сохраняются сразу.</p><strong>{money(query.data?.balances.RUB || 0)}</strong><small>Общий баланс</small></div>{query.isError && <p role="alert">Не удалось обновить обзор. Проверьте соединение.</p>}<div className="tg-section-grid">{sections.map(([path, icon, title, text]) => <NavLink key={path} to={path}><span className="tg-section-icon">{icon}</span><div><h2>{title}</h2><p>{text}</p></div><ArrowUpRight size={18}/></NavLink>)}</div><p className="tg-note">☁️ На VPS приложение работает круглосуточно. На ПК — пока оно запущено.</p></div>;
}
export function TelegramGate({ error }: { error: string }) {
  return <div className="tg-gate"><span className="tg-section-icon">🔐</span><h1>Ваше рабочее пространство</h1><p role="alert">{error || "Откройте Mini App кнопкой в привязанном Telegram-боте. Доступ есть только у владельца."}</p><button className="button" onClick={() => location.reload()}>Повторить подключение</button></div>;
}
