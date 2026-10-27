export interface Account {
  id: string; name: string; token_hint: string; enabled: boolean; status: string;
  error: string | null; info_error: string | null; info: Record<string, any>;
  node_count: number; last_success: number | null; last_attempt: number | null;
  first_seen: number | null; next_refresh: number; pk_hint: string | null; stale: boolean; degraded: boolean;
}
export interface Node {
  id: string; account_id: string; account_name: string; server: string; port: number; region: string;
  region_label: string;
  latency: number | null; reachable: boolean | null; tested_at: number | null; enabled: boolean; stale: boolean;
}
export interface Subscription {
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
