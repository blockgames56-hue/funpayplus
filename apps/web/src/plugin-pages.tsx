import { lazy, Suspense } from "react";
import { Link, useParams } from "react-router-dom";
import {
  PluginCatalog,
  PluginDetailHeader,
  pluginDefinitions,
} from "./plugin-catalog";
import { Empty, Pending } from "./ui";
import { PluginWorkspace } from "./plugin-workspace";
const Kosell = lazy(() => import("./kosell-plugin"));
const KosellRent = lazy(() => import("./kosellrent-plugin"));

const Email = lazy(() =>
  import("./email-code-plugin").then((module) => ({
    default: module.EmailCodePlugin,
  })),
);
const Lzt = lazy(() =>
  import("./lzt-plugin").then((module) => ({ default: module.LztPlugin })),
);
const Fulfillment = lazy(() =>
  import("./fulfillment-plugin").then((module) => ({
    default: module.FulfillmentPlugin,
  })),
);
const AppRoute = lazy(() =>
  import("./infrastructure-plugins").then((module) => ({
    default: module.AppRoutePlugin,
  })),
);
const Offline = lazy(() =>
  import("./infrastructure-plugins").then((module) => ({
    default: module.OfflineActivityPlugin,
  })),
);
const Dump = lazy(() =>
  import("./pricing-plugins").then((module) => ({
    default: module.AutoDumpPlugin,
  })),
);
const Robux = lazy(() =>
  import("./pricing-plugins").then((module) => ({
    default: module.AutoRobuxPlugin,
  })),
);
const Integrated = lazy(() =>
  import("./integrated-plugin").then((module) => ({
    default: module.IntegratedPlugin,
  })),
);

export function PluginsPage() {
  return <PluginCatalog />;
}

export function PluginDetailPage() {
  const { pluginId = "" } = useParams();
  if (!pluginDefinitions.some((plugin) => plugin.id === pluginId))
    return (
      <>
        <Link to="/plugins">Все плагины</Link>
        <Empty
          title="Плагин не найден"
          text="Выберите модуль из каталога. Автоответы находятся в разделе «Автоответы» в меню."
        />
      </>
    );
  if (pluginId === "kosell" || pluginId === "kosell-rent")
    return (
      <div className="plugin-detail">
        <PluginDetailHeader id={pluginId} />
        <Suspense fallback={<Pending />}>
          {pluginId === "kosell-rent" ? <KosellRent /> : <Kosell />}
        </Suspense>
      </div>
    );
  return (
    <div className="plugin-detail">
      <PluginDetailHeader id={pluginId} />
      <PluginWorkspace key={pluginId} id={pluginId}>
        <Suspense fallback={<Pending />}>
          {pluginId === "kosell" && <Kosell />}
          {pluginId === "email-code" && <Email embedded />}
          {pluginId === "lzt" && <Lzt />}
          {pluginId === "discord-boost" && <Fulfillment kind="discord_boost" />}
          {pluginId === "steam-gift" && <Fulfillment kind="steam_gift" />}
          {pluginId === "app-route" && <AppRoute />}
          {pluginId === "offline" && <Offline />}
          {pluginId === "auto-dump" && <Dump />}
          {pluginId === "auto-robux" && <Robux />}
          {[
            "auto-smm",
            "steam-points",
            "steam-rent",
            "auto-stars",
            "support-tickets",
          ].includes(pluginId) && (
            <Integrated
              key={pluginId}
              kind={
                pluginId as
                  | "auto-smm"
                  | "steam-points"
                  | "steam-rent"
                  | "auto-stars"
                  | "support-tickets"
              }
            />
          )}
        </Suspense>
      </PluginWorkspace>
    </div>
  );
}
