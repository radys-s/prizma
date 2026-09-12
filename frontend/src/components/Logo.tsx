import { useId } from "react";

/**
 * Prizma mark: jeden paprsek projde hranolem a **zlomí se**.
 *
 * Dvě předchozí verze kreslily trojúhelník a k němu duhový vějíř vpravo. To je
 * ale doslova obal Dark Side of the Moon — čím líp se to povedlo, tím víc to
 * odkazovalo na album a tím míň na tuhle appku. Znak proto barvu přesunul:
 * **spektrum drží rám hranolu**, ne to, co z něj leze, a jediné, co skrz něj
 * jde, je jeden neutrální paprsek. Tím se z ilustrace fyzikálního jevu stává
 * značka a zároveň to sedí na produkt — jeden dotaz, spousta modelů.
 *
 * Lom je celý pohyb, který ten znak dělá: paprsek přiletí vodorovně, uvnitř
 * skla změní směr a odejde nahoru doprava. Zalomená čára se pozná i na 16 px,
 * kde by se vějíř pěti klínů slil do skvrny.
 */

/** Rám hranolu. Zaoblení rohů obstará `stroke-linejoin`, ne ruční oblouky. */
const PRISM = "M20 6.6 L33.4 30.4 H6.6 Z";

/* Paprsek. Zlom leží uvnitř skla a výstup protíná pravou stěnu skoro kolmo —
   to je celý vtip téhle geometrie. První pokus vedl paprsek výš a odcházel
   téměř rovnoběžně s pravou hranou, takže z lomu zbyl škrábanec přes roh. */
const BEAM = "M2.4 24 H18 L37 11";

export function PrizmaMark({ size = 32, className }: { size?: number; className?: string }) {
  // Id gradientu musí být unikátní na instanci, jinak druhá kopie na stránce
  // tiše použije výplň té první.
  const uid = useId().replace(/:/g, "");

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
        {/* Zdola zleva nahoru doprava: úhlopříčka sedí na tvar trojúhelníku,
            takže se přes rám přelije celé spektrum a žádná barva nezůstane
            schovaná v jednom rohu. */}
        <linearGradient
          id={`${uid}-spectrum`}
          x1="4"
          y1="34"
          x2="36"
          y2="6"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0%" stopColor="var(--spectrum-5)" />
          <stop offset="27%" stopColor="var(--spectrum-4)" />
          <stop offset="52%" stopColor="var(--spectrum-3)" />
          <stop offset="76%" stopColor="var(--spectrum-2)" />
          <stop offset="100%" stopColor="var(--spectrum-1)" />
        </linearGradient>

        <linearGradient
          id={`${uid}-glass`}
          x1="20"
          y1="6"
          x2="20"
          y2="31"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.22" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0.03" />
        </linearGradient>
      </defs>

      {/* Sklo. */}
      <path d={PRISM} fill={`url(#${uid}-glass)`} />

      {/* Paprsek leží pod rámem schválně: barevný obrys je to, podle čeho se
          znak pozná, a přerušený by se poznával hůř. Zpod skla to navíc čte
          jako světlo procházející, ne nalepené navrch. */}
      <path
        d={BEAM}
        stroke="currentColor"
        strokeWidth="3.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />

      {/* Rám. */}
      <path
        d={PRISM}
        stroke={`url(#${uid}-spectrum)`}
        strokeWidth="4"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * Znak v dlaždici — to, čemu se na telefonu říká ikona aplikace.
 *
 * Dlaždice je záměrně neutrální s jemným leskem: pod plnou oranžovou by
 * spektrální rám zanikl, a rám je na tom znaku to jediné, co nese barvu.
 */
export function PrizmaTile({ size = 40, className }: { size?: number; className?: string }) {
  return (
    <span
      className={`prizma-tile${className ? ` ${className}` : ""}`}
      style={{ width: size, height: size, borderRadius: Math.round(size * 0.3) }}
    >
      <PrizmaMark size={Math.round(size * 0.7)} />
    </span>
  );
}

