import { useEffect, useRef, useState } from "react";
import { usePrefersReducedMotion } from "./motion-preferences";
import type { AnimationItem } from "lottie-web";
import { themes, useAppearance } from "./appearance";
import orbit from "./assets/animations/orbit.json";
import signal from "./assets/animations/signal.json";

/** Local SVG animation: no external requests, scripts, expressions or image assets. */
export function LottieMark({
  variant = "orbit",
  className = "",
}: {
  variant?: "orbit" | "signal";
  className?: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const theme = useAppearance((s) => s.theme),
    enabled = useAppearance((s) => s.motion);
  const reduced = usePrefersReducedMotion();
  const accent = themes.find((t) => t.id === theme)!.accent;
  useEffect(() => {
    let disposed = false,
      animation: AnimationItem | undefined,
      observer: IntersectionObserver | undefined;
    let visible = true;
    setReady(false);
    const sync = () => {
      if (!animation) return;
      if (!enabled || reduced) animation.goToAndStop(40, true);
      else if (visible && !document.hidden) animation.play();
      else animation.pause();
    };
    document.addEventListener("visibilitychange", sync);
    void import("lottie-web/build/player/lottie_light")
      .then(({ default: lottie }) => {
        if (disposed || !container.current) return;
        const data = JSON.parse(
          JSON.stringify(variant === "orbit" ? orbit : signal),
        );
        const color = [1, 3, 5].map(
          (i) => parseInt(accent.slice(i, i + 2), 16) / 255,
        );
        for (const layer of data.layers)
          for (const shape of layer.shapes) {
            if (shape.c?.k && Math.abs(shape.c.k[0] - 0.063) < 0.01)
              shape.c.k = [...color, 1];
          }
        animation = lottie.loadAnimation({
          container: container.current,
          renderer: "svg",
          loop: true,
          autoplay: false,
          animationData: data,
          rendererSettings: {
            preserveAspectRatio: "xMidYMid meet",
            progressiveLoad: true,
          },
        });
        animation.addEventListener("DOMLoaded", () => {
          if (!disposed) {
            setReady(true);
            sync();
          }
        });
        if (typeof IntersectionObserver !== "undefined") {
          observer = new IntersectionObserver((entries) => {
            visible = entries[0]?.isIntersecting ?? true;
            sync();
          });
          observer.observe(container.current);
        }
        sync();
      })
      .catch(() => {
        /* The static poster remains if the optional animation cannot load. */
      });
    return () => {
      disposed = true;
      observer?.disconnect();
      document.removeEventListener("visibilitychange", sync);
      animation?.destroy();
    };
  }, [variant, accent, enabled, reduced]);
  return (
    <div
      className={`lottie-mark ${className}`}
      data-lottie={variant}
      aria-hidden="true"
    >
      {!ready && (
        <svg className="lottie-poster" viewBox="0 0 240 240">
          <circle
            cx="120"
            cy="120"
            r="83"
            fill="none"
            stroke="currentColor"
            opacity=".25"
          />
          <path d="M129 88 99 125h18l-6 27 30-37h-18Z" fill="currentColor" />
        </svg>
      )}
      <div ref={container} className="lottie-canvas" />
    </div>
  );
}

export function MotionBanner({
  title,
  text,
  label = "ВАШ МАГАЗИН · ВАШ РИТМ",
}: {
  title: string;
  text: string;
  label?: string;
}) {
  return (
    <section className="motion-banner">
      <div className="banner-copy">
        <span className="eyebrow">
          <i />
          {label}
        </span>
        <h2>{title}</h2>
        <p>{text}</p>
      </div>
      <LottieMark />
      <div className="banner-orb" />
    </section>
  );
}
