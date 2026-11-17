export interface Account {
  id: string; name: string; token_hint: string; enabled: boolean; status: string;
  error: string | null; info_error: string | null; info: Record<string, any>;
  node_count: number; last_success: number | null; last_attempt: number | null;
  first_seen: number | null; next_refresh: number; pk_hint: string | null; stale: boolean; degraded: boolean;
}
export interface Node {
  id: string; account_id: string; account_name: string; server: string; port: number; region: string;
  region_label: string; region_country: string; region_name: string; official_name: string | null;
  latency: number | null; reachable: boolean | null; tested_at: number | null; enabled: boolean; stale: boolean;
}
export interface Subscription {
  chain_ids: string[]; chain_mode: "native" | "relay";
  id: string; name: string; account_ids: string[]; source_ids: string[]; regions: string[];
  exclude_failed: boolean; dedupe: boolean; max_latency: number | null;
  max_per_country: number | null; last_probe: number | null;
  enabled: boolean; clash_url: string; proxies_url: string;
}
export interface Source {
  id: string; name: string; address: string; kind: 'url' | 'file'; enabled: boolean; node_count: number;
  last_fetch: number | null; last_error: string | null; failures: number; created_at: number;
  invalid: boolean;
}
export interface SubNode {
  key: string; name: string; type: string; server: string; port: number; group: string; origin: string;
  country: string; country_label: string; reachable: boolean | null; latency: number | null;
  tested_at: number | null; method: string | null; included: boolean; reason: string | null;
}
export interface Job {
  id: string; kind: string; status: string; total: number; completed: number; errors: string[]; created_at: number;
}
export interface Preferences {
  proxy_public_host: string;
  proxy_port_start: number; proxy_port_end: number;
  refresh_minutes: number; probe_minutes: number; probe_timeout_ms: number;
  mihomo: boolean; public_base_url: string; extension_version: string; version: string;
}

export interface Chain {
  id: string; name: string; entry_id: string; exit_id: string;
  entry_mode: 'auto' | 'fixed'; entry_node_key: string | null; selected_entry?: string | null;
  enabled: boolean; relay_enabled: boolean; listen_port: number; public_host: string; probe?: ProbeResult | null;
  status: { state: string; error?: string; current_entry?: string | null; current_entry_key?: string | null; latency?: number | null };
}

export type ThemePreference = 'light' | 'dark' | 'system'
export interface ProbeResult {
  reachable: boolean; latency: number | null; entry?: string | null; entry_key?: string | null; error: string | null; tested_at: number;
}
export interface Entry {
  id: string; name: string; subscription_id: string; mode: 'auto' | 'fixed'; node_key: string | null; node_keys: string[]; enabled: boolean;
}
export interface Exit {
  id: string; name: string; type: 'http' | 'https' | 'socks5'; server: string; port: number;
  username?: string; tls?: boolean; enabled: boolean; has_password: boolean; probe?: ProbeResult | null;
}
export interface ConnectionInfo {
  host: string; port: number; username: string; password: string; protocols: string[];
}
export interface MenuAction {
  key: string; label: string; danger?: boolean; disabled?: boolean; divided?: boolean;
}
