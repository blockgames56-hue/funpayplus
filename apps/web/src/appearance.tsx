import { usePrefersReducedMotion } from "./motion-preferences";
import { MotionConfig } from "motion/react";
import type { ReactNode } from "react";
import { create } from "zustand";
import { persist } from "zustand/middleware";
import { Check, Monitor, Moon, Sun, Type } from "lucide-react";
import { Toggle } from "./ui";

export const themes = [
  {
    id: "dark-blue",
    name: "Тёмная синяя",
    description: "Чёрный фон и яркий синий акцент",
    background: "#0b0d11",
    panel: "#161a21",
    ink: "#eef3ff",
    accent: "#0877ff",
  },
  {
    id: "light-blue",
    name: "Светлая синяя",
    description: "Светлые карточки и глубокий синий",
    background: "#f6f8fc",
    panel: "#ffffff",
    ink: "#101c38",
    accent: "#005de8",
  },
  {
    id: "light-mono",
    name: "Светлая монохромная",
    description: "Белый, графит и чёткие границы",
    background: "#fafafa",
    panel: "#ffffff",
    ink: "#191919",
    accent: "#191919",
  },
  {
    id: "dark-mono",
    name: "Тёмная монохромная",
    description: "Угольные поверхности и белый акцент",
    background: "#101010",
    panel: "#1c1c1c",
    ink: "#f5f5f5",
    accent: "#eeeeee",
  },
  {
    id: "mint",
    name: "Тёмная мятная",
    description: "Привычная тема FunPay Plus",
    background: "#090a0f",
    panel: "#111420",
    ink: "#e5edf7",
    accent: "#10b981",
  },
] as const;
type Theme = (typeof themes)[number]["id"];
export const useAppearance = create(
  persist<{
    theme: Theme;
    textSize: "normal" | "large";
    motion: boolean;
    set: (
      value: Partial<{
        theme: Theme;
        textSize: "normal" | "large";
        motion: boolean;
      }>,
    ) => void;
  }>((set) => ({ theme: "mint", textSize: "normal", motion: true, set }), {
    name: "funpay-plus-appearance",
    merge: (saved, current) => {
      const value = saved as Partial<typeof current> | undefined;
      return {
        ...current,
        theme: themes.some((t) => t.id === value?.theme)
          ? value!.theme!
          : current.theme,
        textSize: value?.textSize === "large" ? "large" : "normal",
        motion: value?.motion !== false,
      };
    },
  }),
);

function apply() {
  const { theme, textSize, motion } = useAppearance.getState();
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.textSize = textSize;
  document.documentElement.dataset.motion = motion ? "on" : "off";
  document.documentElement.style.colorScheme = theme.startsWith("light")
    ? "light"
    : "dark";
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", themes.find((t) => t.id === theme)!.background);
}
apply();
useAppearance.subscribe(apply);

export function AppearanceSettings() {
  const state = useAppearance();
  return (
    <section
      id="appearance"
      className="panel appearance-settings"
      aria-label="Внешний вид"
    >
      <div className="panel-head">
        <div>
          <h2>
            <Monitor size={20} />
            Внешний вид
          </h2>
          <p className="muted">
            Тема применяется сразу ко всему приложению и сохраняется на этом
            устройстве.
          </p>
        </div>
      </div>
      <div
        className="appearance-grid"
        role="radiogroup"
        aria-label="Тема приложения"
      >
        {themes.map((t) => (
          <button
            key={t.id}
            role="radio"
            data-theme-choice={t.id}
            tabIndex={state.theme === t.id ? 0 : -1}
            onKeyDown={(e) => {
              if (
                ![
                  "ArrowLeft",
                  "ArrowRight",
                  "ArrowUp",
                  "ArrowDown",
                  "Home",
                  "End",
                ].includes(e.key)
              )
                return;
              e.preventDefault();
              const index = themes.findIndex((v) => v.id === t.id);
              const target =
                e.key === "Home"
                  ? 0
                  : e.key === "End"
                    ? themes.length - 1
                    : (index +
                        (["ArrowLeft", "ArrowUp"].includes(e.key) ? -1 : 1) +
                        themes.length) %
                      themes.length;
              const next = themes[target].id;
              state.set({ theme: next });
              document
                .querySelector<HTMLButtonElement>(
                  `[data-theme-choice="${next}"]`,
                )
                ?.focus();
            }}
            aria-checked={state.theme === t.id}
            onClick={() => state.set({ theme: t.id })}
            className="theme-choice"
            style={
              {
                "--preview-bg": t.background,
                "--preview-panel": t.panel,
                "--preview-ink": t.ink,
                "--preview-accent": t.accent,
              } as React.CSSProperties
            }
          >
            <div className="theme-preview" aria-hidden="true">
              <aside>
                <i />
                <i />
                <i />
              </aside>
              <div>
                <header />
                <section>
                  <b />
                  <b />
                  <b />
                </section>
                <footer />
              </div>
            </div>
            <div className="theme-choice-label">
              {t.id.startsWith("light") ? (
                <Sun size={16} />
              ) : (
                <Moon size={16} />
              )}
              <strong>{t.name}</strong>
              {state.theme === t.id && <Check size={17} />}
            </div>
            <small>{t.description}</small>
          </button>
        ))}
      </div>
      <div className="appearance-options">
        <div className="setting-row">
          <div>
            <strong>
              <Type size={16} /> Размер текста
            </strong>
            <p>Также увеличивает подписи, поля и кнопки.</p>
          </div>
          <select
            aria-label="Размер текста"
            value={state.textSize}
            onChange={(e) =>
              state.set({ textSize: e.target.value as "normal" | "large" })
            }
          >
            <option value="normal">Обычный</option>
            <option value="large">Крупный</option>
          </select>
        </div>
        <div className="setting-row">
          <div>
            <strong>Анимации интерфейса</strong>
            <p>Системная настройка уменьшения движения также учитывается.</p>
          </div>
          <Toggle
            label="Анимации интерфейса"
            checked={state.motion}
            onChange={(motion) => state.set({ motion })}
          />
        </div>
      </div>
    </section>
  );
}

export function AppearanceMotion({ children }: { children: ReactNode }) {
  const motion = useAppearance((s) => s.motion);
  const reduced = usePrefersReducedMotion();
  return (
    <MotionConfig reducedMotion={motion && !reduced ? "never" : "always"}>
      {children}
    </MotionConfig>
  );
}
