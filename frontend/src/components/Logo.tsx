import { useId } from "react";

/**
 * The Prizma mark: one beam enters a prism on the left and leaves as a
 * spectrum on the right — one prompt in, the right model out. The prism itself
 * is drawn in `currentColor` so it inherits whatever context it sits in; the
 * fan is the only place colour is spent, which keeps it legible at 20px.
 */
export function PrizmaMark({ size = 32, className }: { size?: number; className?: string }) {
  // Gradient ids must be unique per instance or a second copy on the page
  // silently reuses the first one's fill.
  const uid = useId().replace(/:/g, "");

  const rays: Array<{ to: [number, number]; color: string; width: number }> = [
    { to: [40, 10.5], color: `url(#${uid}-a)`, width: 2.1 },
    { to: [40, 15.5], color: `url(#${uid}-b)`, width: 2.1 },
    { to: [40, 20.5], color: `url(#${uid}-c)`, width: 2.1 },
    { to: [40, 25.5], color: `url(#${uid}-d)`, width: 2.1 },
    { to: [40, 30.5], color: `url(#${uid}-e)`, width: 2.1 },
  ];

  // Colours come from the design tokens, so the mark and the login aurora
  // never drift apart.
  const stops: Array<[string, string]> = [
    [`${uid}-a`, "var(--spectrum-1)"],
    [`${uid}-b`, "var(--spectrum-2)"],
    [`${uid}-c`, "var(--spectrum-3)"],
    [`${uid}-d`, "var(--spectrum-4)"],
    [`${uid}-e`, "var(--spectrum-5)"],
  ];

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      className={className}
      role="img"
      aria-label="Prizma"
    >
      <defs>
        {stops.map(([id, color]) => (
          // Each ray fades in from the exit point rather than starting hard,
          // so the light looks like it's emerging instead of being drawn on.
          <linearGradient key={id} id={id} x1="26" y1="0" x2="40" y2="0" gradientUnits="userSpaceOnUse">
            <stop offset="0%" stopColor={color} stopOpacity="0.15" />
            <stop offset="35%" stopColor={color} stopOpacity="1" />
            <stop offset="100%" stopColor={color} stopOpacity="0.85" />
          </linearGradient>
        ))}
        <linearGradient id={`${uid}-glass`} x1="20" y1="6" x2="20" y2="32" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.20" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0.04" />
        </linearGradient>
      </defs>

      {/* incoming beam */}
      <path
        d="M1 19.5 H13"
        stroke="currentColor"
        strokeWidth="2.1"
        strokeLinecap="round"
        opacity="0.9"
      />

      {/* the prism */}
      <path d="M20 6.5 L33.5 31.5 H6.5 Z" fill={`url(#${uid}-glass)`} />
      <path
        d="M20 6.5 L33.5 31.5 H6.5 Z"
        stroke="currentColor"
        strokeWidth="2.1"
        strokeLinejoin="round"
      />

      {/* refracted fan */}
      <g strokeLinecap="round">
        {rays.map((ray) => (
          <path
            key={ray.to[1]}
            d={`M26.6 20.5 L${ray.to[0]} ${ray.to[1]}`}
            stroke={ray.color}
            strokeWidth={ray.width}
          />
        ))}
      </g>
    </svg>
  );
}

/** Mark + name, for the login screen and the sidebar header. */
export function PrizmaWordmark({ size = 28 }: { size?: number }) {
  return (
    <span className="wordmark">
      <PrizmaMark size={size} />
      <span className="wordmark-text">Prizma</span>
    </span>
  );
}
