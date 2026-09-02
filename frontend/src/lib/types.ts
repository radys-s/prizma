export type OAuthProviderId = "google" | "github" | "apple" | "microsoft";

/** One way of signing in. The account may have several; it always has one. */
export interface AuthIdentity {
  id: number;
  provider: string;
  email: string | null;
  display_name: string | null;
  created_at: string;
}

export interface AuthUser {
  display_name: string | null;
  email: string | null;
  provider: string;
  /** Whether a code sent to `email` has ever been typed back in. */
  email_verified: boolean;
  /** Whether an authenticator app is set up and confirmed. */
  totp_confirmed: boolean;
  recovery_codes_left: number;
}

export interface AuthState {
  /** False on a fresh install — the login screen asks to *create* a passcode. */
  has_account: boolean;
  authenticated: boolean;
  user: AuthUser | null;
  /** Empty unless authenticated; the settings panel lists these. */
  identities: AuthIdentity[];
  /** Which social buttons are actually wired up in .env. */
  oauth: Record<OAuthProviderId, boolean>;
  /** False once OWNER_EMAIL-gated deploys switch the code path off. */
  passcode_enabled: boolean;
  /** False on a server with no mail provider — the email form is then a lie. */
  email_enabled: boolean;
  min_passcode_length: number;
  min_password_length: number;
  /** Seconds left on the brute-force lockout; 0 when not locked. */
  locked_for_seconds: number;
}

/** What `/email/login` and `/email/register` answer with: never a session. */
export interface TwoFactorChallenge {
  stage: "2fa";
  challenge_id: number;
  /** "email" = a code was mailed; "totp" = read it off the authenticator app. */
  method: "email" | "totp";
  /** Masked, e.g. `to…11@gmail.com`. Safe to render on a public login page. */
  email: string;
}

export interface Provider {
  id: string;
  display_name: string;
  /** Preferred slug — what runs unless the provider has retired it. */
  model: string;
  /** Ordered fallbacks; the router walks these when one 404s. */
  models: string[];
  capabilities: string[];
  requires_card: boolean;
  notes: string;
  signup_url: string;
  key_prefix: string;
  /** Credential-store entry. Providers sharing one account key share this. */
  key_id: string;
  configured: boolean;
}

/** What this machine can realistically run, as `/api/providers/local` reports it. */
export interface LocalHardware {
  /** Null when the OS wouldn't say — the UI then shows no verdict at all. */
  ram_gb: number | null;
  cpu_cores: number;
  /** Drives the colour of the warning, nothing else. */
  level: "unknown" | "no" | "tight" | "ok" | "good";
  verdict: string;
  /** Largest model size worth trying, e.g. "do ~7B v Q4". */
  ceiling: string;
  gpu_note: string;
}

export interface LocalStatus {
  /** False on a public server: the backend refuses to fetch a typed-in URL
   *  there, so the whole section hides rather than offering a dead button. */
  available: boolean;
  configured: boolean;
  base_url: string;
  model: string;
  label: string;
  hardware: LocalHardware;
}

export interface KeyTestResult {
  /** Whether the *key* authenticated — not whether the request succeeded. */
  ok: boolean;
  /** "warning" covers a valid key blocked by quota, billing or a retired model. */
  level: "ok" | "warning" | "error";
  detail: string;
}

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  /** Pinned threads sort above every day bucket in the sidebar. */
  pinned: boolean;
}

/** One row of `GET /api/conversations/search` — a hit in a message *body*. */
export interface MessageHit {
  conversation_id: number;
  title: string;
  role: string;
  /** ±60 characters around the match, with ellipses where it was cut. */
  snippet: string;
}

/** One page the searcher actually read. `[n]` in the answer points here. */
export interface Source {
  title: string;
  url: string;
  /** Only Exa and Linkup return one. Absent everywhere else — the UI falls
   *  back to a hostname monogram rather than asking a favicon CDN, which
   *  would hand a third party the list of pages Prizma read. */
  favicon?: string;
}

/** Globe control — mirrors `research.MODES` on the backend. */
export type SearchMode = "auto" | "web" | "deep" | "off";

export interface Message {
  id?: number;
  conversation_id?: number;
  role: "user" | "assistant" | "system";
  content: string;
  provider_id?: string | null;
  model_name?: string | null;
  created_at?: string;
  streaming?: boolean;
  error?: boolean;

  /** How the router got to this answer. Null on messages written before v2. */
  capability?: string | null;
  complexity?: string | null;
  effort?: string | null;
  /** Which model planned it; null means the heuristic decided. */
  planner_model?: string | null;
  /** Pinned provider that couldn't answer, so someone else did. */
  substituted_from?: string | null;
  /** Went through the max-tier review pass. */
  revised?: boolean;
  /** Live-only: shown while the review pass is rewriting the bubble. */
  reviewing?: boolean;
  /** Live-only: the one-line note from the meta event. */
  note?: string;
  /** Live-only: the planner's one-sentence read of what was wanted. */
  goal?: string;

  /** Which search depth actually ran. Null means the answer never hit the web. */
  search_mode?: string | null;
  /** Pages the searcher read, in citation order. JSON text in the DB. */
  sources?: Source[] | null;
  /** Live-only: what is being searched right now, one entry per query. */
  searching?: string[] | null;
  /** Live-only: the planner has answered. Drives the first stepper tick. */
  planned?: boolean;
  /** Live-only: every query the search stage asked, kept after it finishes so
   *  the stepper can still say how many there were. */
  search_queries?: string[] | null;
  /** Live-only: why the search stage gave up, shown instead of the chip. */
  search_error?: string;
  /** Live-only: pages being fetched in full, deep mode only. */
  reading?: string[] | null;
  /** Live-only: what auto-compact folded away before this answer ran. */
  compact?: CompactInfo | null;

  /** Wall clock from request to last token. Null on pre-v3 rows. */
  latency_ms?: number | null;
  /** Total tokens, when the provider reported any. Null when it didn't. */
  tokens?: number | null;
}

/** One entry in the model picker. `auto` is always first and recommended. */
export interface ModelChoice {
  id: string;
  label: string;
  description: string;
  configured: boolean;
  recommended: boolean;
}

/** One entry in the effort picker — mirrors backend/app/routing/effort.py. */
export interface EffortLevel {
  id: string;
  label: string;
  description: string;
}

/** One entry in the globe picker — mirrors `_SEARCH_MODES` in api/routing.py. */
export interface SearchOption {
  id: string;
  label: string;
  description: string;
  /** False when no search-capable key is stored, so the mode would abort. */
  available: boolean;
}

export interface RoutingOptions {
  models: ModelChoice[];
  efforts: EffortLevel[];
  searches: SearchOption[];
  default_effort: string;
  default_search: string;
  /** Whether anything can reach the web. Always true — the keyless
   *  Wikipedia/arXiv floor is permanently in the chain. */
  can_search: boolean;
  /** How many *keyed* searchers are connected. Zero means the floor is all
   *  there is, which searching still works on but answers very differently. */
  search_keyed: number;
  /** Said out loud when `search_keyed` is 0, empty otherwise. */
  search_hint: string;
  /** Filled credential slots, and how many the app insists on. */
  configured_slots: number;
  min_slots: number;
}

/** The `compact` event: the older turns were folded into a summary. */
export interface CompactInfo {
  /** Messages from the start of the thread the summary now stands in for. */
  folded: number;
  /** Messages still sent verbatim. */
  kept: number;
  /** The summariser failed, so the prefix was simply dropped instead. */
  fallback: boolean;
}

/** What the front model decided, streamed before the answer starts. */
export interface RoutingPlan {
  capability: string;
  complexity: string;
  goal: string;
  /** "model" when a planner ran, "heuristic" when it fell back. */
  source: string;
  planner_model: string | null;
  effort: string;
  /** The planner's own call on whether this needs the web. */
  needs_search: boolean;
  /** The depth that was resolved from it and the globe control. */
  search: string;
}

/** `GET /api/settings` — app-level switches that outlive a browser session. */
export interface AppSettings {
  /** Deep research fetches the top hits in full through Jina Reader. */
  read_pages: boolean;
  /** Standing system prompt, prepended to every answer. Empty = none. */
  custom_instructions: string;
  /** Server-owned cap, so the counter in the dialog can't drift from it. */
  custom_instructions_max: number;
}
