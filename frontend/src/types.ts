export interface Account {
  id: string; name: string; token_hint: string; enabled: boolean; status: string;
  error: string | null; info_error: string | null; info: Record<string, any>;
  node_count: number; last_success: number | null; last_attempt: number | null;
  first_seen: number | null; next_refresh: number; pk_hint: string | null; stale: boolean; degraded: boolean;
}
export interface Node {
  id: string; account_id: string; account_name: string; server: string; port: number; region: string;
  latency: number | null; reachable: boolean | null; tested_at: number | null; enabled: boolean; stale: boolean;
}
export interface Subscription {
  id: string; name: string; account_ids: string[]; regions: string[]; exclude_failed: boolean;
  enabled: boolean; clash_url: string; proxies_url: string;
}
export interface Job {
  id: string; kind: string; status: string; total: number; completed: number; errors: string[]; created_at: number;
}
