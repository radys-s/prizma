/**
 * SQLite drops the timezone, so the API hands back naive UTC timestamps like
 * `2026-08-28T18:04:05.123456`. Parsing those directly would make the browser
 * read them as local time and shift every conversation by the UTC offset.
 */
export function parseServerDate(value: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  return new Date(hasZone ? value : `${value}Z`);
}

export function dayBucket(value: string): string {
  const date = parseServerDate(value);
  if (Number.isNaN(date.getTime())) return "Starší";

  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const startOfDate = new Date(date);
  startOfDate.setHours(0, 0, 0, 0);

  // Rounded whole days between midnights, so a DST shift can't skew the bucket.
  const days = Math.round((startOfToday.getTime() - startOfDate.getTime()) / 86_400_000);

  if (days <= 0) return "Dnes";
  if (days === 1) return "Včera";
  if (days < 7) return "Posledních 7 dní";
  return "Starší";
}

/** Strips diacritics so "cesko" also matches "Česko". */
export function normalize(value: string): string {
  return value
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "");
}

const ABSOLUTE = new Intl.DateTimeFormat("cs-CZ", {
  dateStyle: "medium",
  timeStyle: "short",
});

/** "před 5 min" / "před 3 h" / "28. 8. 2026". Empty string on an unparsable value. */
export function relativeTime(value: string): string {
  const date = parseServerDate(value);
  if (Number.isNaN(date.getTime())) return "";

  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  // A clock a few seconds behind the server would otherwise read "za 4 s".
  if (seconds < 60) return "právě teď";
  if (seconds < 3600) return `před ${Math.floor(seconds / 60)} min`;
  if (seconds < 86_400) return `před ${Math.floor(seconds / 3600)} h`;
  if (seconds < 604_800) {
    const days = Math.floor(seconds / 86_400);
    return `před ${days} ${days === 1 ? "dnem" : "dny"}`;
  }
  return date.toLocaleDateString("cs-CZ");
}

/** The full stamp, for the tooltip behind the relative one. */
export function absoluteTime(value: string): string {
  const date = parseServerDate(value);
  return Number.isNaN(date.getTime()) ? "" : ABSOLUTE.format(date);
}
