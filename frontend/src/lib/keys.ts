import type { Provider } from "./types";

/** Same rule as the backend's `selector._can_answer`: search-only entries cannot. */
export function canAnswer(provider: Pick<Provider, "capabilities">): boolean {
  return provider.capabilities.some((c) => c !== "search");
}

/**
 * Answering credential slots, counted the way the router counts them: one per
 * account key, search-only keys left out. This is what the "aspoň 2" minimum
 * is measured against, so every counter on screen uses it.
 */
export function answeringSlots(providers: Provider[]): { configured: number; total: number } {
  const answering = providers.filter(canAnswer);
  return {
    configured: new Set(answering.filter((p) => p.configured).map((p) => p.key_id)).size,
    total: new Set(answering.map((p) => p.key_id)).size,
  };
}
