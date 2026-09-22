/**
 * Typed client for the Python indexer.
 *
 * NOTE: this Next.js app is optional. The indexer now serves the same pages
 * itself in Python (no Node required) - see indexer/stackapp_indexer/web.
 * The JSON API lives under /api because the bare paths are those HTML pages.
 */

export const INDEXER_URL =
  process.env.NEXT_PUBLIC_INDEXER_URL ?? "http://127.0.0.1:8787";
export const INDEXER_WS =
  process.env.NEXT_PUBLIC_INDEXER_WS ?? "ws://127.0.0.1:8787/ws";

export type PoolView = {
  totalCollected: number;
  totalClaimed: number;
  undistributed: number;
  outstanding: number;
  totalWeightedShares: number;
  /** u128 - a string, because JSON numbers lose precision past 2^53. */
  accRewardPerShare: string;
};

export type TokenView = {
  mint: string;
  creator: string;
  registeredAt: number;
  ageSeconds: number;
  depositVault: string;
  vaultLamports: number;
  registrationMarkerLamports: number;
  totalMarkerDeposits: number;
  totalRentSpent: number;
  /** Money sitting in the vault that neither `donate` nor the marker/rent
   * bookkeeping explains - what `reconcile` would sweep into the pool right now. */
  pendingReconcileSurplus: number;
  pool: PoolView;
  registrationCount: number;
  pendingRegistrationCount: number;
};

export type RegistrationView = {
  mint: string;
  owner: string;
  registeredAt: number;
  ageSeconds: number;
  tenureMultiplierBps: number;
  weightedShares: number;
  poolSharePpm: number;
  projectedClaimable: number;
  lifetimeRewardsClaimed: number;
  claimEligibleAtSlot: number;
  claimEligible: boolean;
};

export type PendingRegistration = {
  mint: string;
  owner: string;
  amount: number;
  slot: number;
  signature: string;
  detectedAt: number;
};

export type FeedEvent = {
  name: string;
  data: Record<string, any>;
  slot: number;
  signature: string;
  receivedAt: number;
};

export type PdaSet = {
  programId: string;
  globalConfig: { address: string; bump: number };
  tokenConfig: { address: string; bump: number };
  loyaltyPool: { address: string; bump: number };
  depositVault: { address: string; bump: number };
  registration?: { address: string; bump: number };
};

export class IndexerError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "IndexerError";
  }
}

async function get<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${INDEXER_URL}${path}`, { cache: "no-store", ...init });
  } catch (cause) {
    throw new IndexerError(
      `Cannot reach the indexer at ${INDEXER_URL}. Is it running? ` +
        `(cd indexer && python -m stackapp_indexer --mock)`
    );
  }
  if (!response.ok) {
    throw new IndexerError(
      `${path} returned ${response.status} ${response.statusText}`,
      response.status
    );
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => get<Record<string, any>>("/api/health"),
  tokens: () => get<TokenView[]>("/api/tokens"),
  token: (mint: string) => get<TokenView>(`/api/tokens/${mint}`),
  registrationsForMint: (mint: string, limit = 50) =>
    get<RegistrationView[]>(`/api/tokens/${mint}/registrations?limit=${limit}`),
  pendingForMint: (mint: string) => get<PendingRegistration[]>(`/api/tokens/${mint}/pending`),
  registrationsForOwner: (owner: string) =>
    get<RegistrationView[]>(`/api/registrations/${owner}`),
  registration: (mint: string, owner: string) =>
    get<RegistrationView>(`/api/registrations/${mint}/${owner}`),
  feed: (opts: { limit?: number; mint?: string; owner?: string; kinds?: string[] } = {}) => {
    const params = new URLSearchParams();
    if (opts.limit) params.set("limit", String(opts.limit));
    if (opts.mint) params.set("mint", opts.mint);
    if (opts.owner) params.set("owner", opts.owner);
    for (const kind of opts.kinds ?? []) params.append("kind", kind);
    const query = params.toString();
    return get<FeedEvent[]>(`/api/feed${query ? `?${query}` : ""}`);
  },
  pdas: (mint: string, owner?: string) =>
    get<PdaSet>(`/api/pdas/${mint}${owner ? `?owner=${owner}` : ""}`),
};

/**
 * Live feed socket with backoff. Returns an unsubscribe function.
 *
 * The server sends `{type:"backlog"}` once on connect, then `{type:"event"}`
 * per event and `{type:"ping"}` as a keepalive.
 */
export function subscribeFeed(
  onEvent: (event: FeedEvent) => void,
  onBacklog?: (events: FeedEvent[]) => void,
  onStatus?: (status: "connecting" | "open" | "closed") => void
): () => void {
  let socket: WebSocket | null = null;
  let closed = false;
  let backoff = 1000;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const connect = () => {
    if (closed) return;
    onStatus?.("connecting");
    socket = new WebSocket(INDEXER_WS);

    socket.onopen = () => {
      backoff = 1000;
      onStatus?.("open");
    };

    socket.onmessage = (message) => {
      try {
        const payload = JSON.parse(message.data as string);
        if (payload.type === "event") onEvent(payload.event as FeedEvent);
        else if (payload.type === "backlog") onBacklog?.(payload.events as FeedEvent[]);
      } catch {
        /* ignore malformed frames */
      }
    };

    socket.onclose = () => {
      onStatus?.("closed");
      if (closed) return;
      timer = setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 15_000);
    };

    socket.onerror = () => socket?.close();
  };

  connect();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    socket?.close();
  };
}
