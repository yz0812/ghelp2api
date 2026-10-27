export let csrf = ''
export function setCsrf(value: string) { csrf = value }

export async function api(path: string, method = 'GET', body?: unknown): Promise<any> {
  const res = await fetch(`/api/v1${path}`, {
    method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    ...(body === undefined ? {} : { body: JSON.stringify(body) })
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    if (res.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('session-expired'))
    const detail = Array.isArray(data.detail) ? data.detail.map((x: any) => `${x.loc?.slice(1).join('.')}: ${x.msg}`).join('；') : data.detail
    throw new Error(detail || `请求失败 (${res.status})`)
  }
  return res.headers.get('content-type')?.includes('json') ? res.json() : res.text()
}
