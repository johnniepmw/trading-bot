// Feature flags (docs/feature-flags.md). Resolution: env FLAG_<NAME> → remote override
// (per-environment JSON in Cloud Storage, cached 30s) → registry default in flags.json.
// If the remote source fails, the last good overrides are kept.
import { readFileSync } from "node:fs";

export type FlagType = "release" | "kill-switch" | "ops";
export interface FlagSpec {
  type: FlagType;
  default: boolean;
}
export type Registry = Record<string, FlagSpec>;
export type Overrides = Record<string, boolean>;
export interface FlagSource {
  fetchOverrides(): Promise<Overrides>;
}

const REFRESH_MS = 30_000;
const TRUE = new Set(["1", "true", "on", "yes"]);
const FALSE = new Set(["0", "false", "off", "no"]);

export function loadRegistry(path = "flags.json"): Registry {
  const parsed = JSON.parse(readFileSync(path, "utf8")) as { flags: Registry };
  return parsed.flags;
}

export function envOverride(name: string, env: Record<string, string | undefined>): boolean | undefined {
  const raw = env[`FLAG_${name.toUpperCase().replaceAll("-", "_")}`];
  if (raw === undefined) return undefined;
  const value = raw.trim().toLowerCase();
  if (TRUE.has(value)) return true;
  if (FALSE.has(value)) return false;
  return undefined;
}

export class Flags {
  private readonly registry: Registry;
  private readonly source: FlagSource | undefined;
  private readonly now: () => number;
  private readonly env: Record<string, string | undefined>;
  private overrides: Overrides = {};
  private fetchedAt = Number.NEGATIVE_INFINITY;
  private inFlight: Promise<void> | undefined;

  constructor(
    registry: Registry,
    options: {
      source?: FlagSource;
      now?: () => number;
      env?: Record<string, string | undefined>;
    } = {},
  ) {
    this.registry = registry;
    this.source = options.source;
    this.now = options.now ?? Date.now;
    this.env = options.env ?? process.env;
  }

  /** Is the feature enabled? Throws for unregistered names (CI also checks this). */
  isEnabled(name: string): boolean {
    const spec = this.registry[name];
    if (spec === undefined) throw new Error(`Unknown flag "${name}": add it to flags.json`);
    if (this.source && this.now() - this.fetchedAt > REFRESH_MS) void this.refresh();
    return envOverride(name, this.env) ?? this.overrides[name] ?? spec.default;
  }

  /** Fetch remote overrides now. Keeps the last good values on failure. */
  async refresh(): Promise<void> {
    const source = this.source;
    if (!source) return;
    this.inFlight ??= (async () => {
      try {
        this.overrides = await source.fetchOverrides();
        this.fetchedAt = this.now();
      } catch (error) {
        console.error(JSON.stringify({ severity: "WARNING", message: "flag refresh failed", error: String(error) }));
        this.fetchedAt = this.now(); // back off for one interval
      } finally {
        this.inFlight = undefined;
      }
    })();
    return this.inFlight;
  }
}

/** Remote overrides from gs://<bucket>/flags.json using the Cloud Run identity. */
export function gcsSource(bucket: string, fetchImpl: typeof fetch = fetch): FlagSource {
  return {
    async fetchOverrides(): Promise<Overrides> {
      const tokenResponse = await fetchImpl(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        { headers: { "Metadata-Flavor": "Google" } },
      );
      if (!tokenResponse.ok) throw new Error(`metadata token: HTTP ${tokenResponse.status}`);
      const { access_token: token } = (await tokenResponse.json()) as { access_token: string };
      const url = `https://storage.googleapis.com/storage/v1/b/${encodeURIComponent(bucket)}/o/flags.json?alt=media`;
      const response = await fetchImpl(url, { headers: { Authorization: `Bearer ${token}` } });
      if (response.status === 404) return {};
      if (!response.ok) throw new Error(`flags object: HTTP ${response.status}`);
      const body = (await response.json()) as { overrides?: Record<string, unknown> };
      const entries = Object.entries(body.overrides ?? {}).filter(
        (entry): entry is [string, boolean] => typeof entry[1] === "boolean",
      );
      return Object.fromEntries(entries);
    },
  };
}
