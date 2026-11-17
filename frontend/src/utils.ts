import { ElMessage } from 'element-plus'
import type { Chain, ConnectionInfo, SubNode } from './types'

export const stateLabel: Record<string, string> = {
  pending: '等待获取', ready: '已就绪', error: '获取失败', invalid: '凭证失效',
  running: '进行中', completed: '已完成', partial: '部分失败', interrupted: '已中断', done: '检测完成', cancelled: '已取消', failed: '执行失败'
}

export const jobKind: Record<string, string> = {
  chain_probe: '链路检测', chain_diagnostic: '路径诊断', refresh: '账号刷新', probe: '节点测速', sub_probe: '订阅测速', source: '订阅源检测'
}

export function time(value: number | null) {
  return value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '—'
}

export function bytes(value: unknown) {
  if (typeof value !== 'number') return '上游未提供'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let n = value, i = 0
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(i ? 2 : 0)} ${units[i]}`
}

export function hostOf(address: string) {
  try { return new URL(address).hostname } catch { return '' }
}

export function latencyClass(node: SubNode) {
  return node.latency === null ? '' : node.latency <= 200 ? 'latency-good' : node.latency <= 500 ? 'latency-fair' : 'latency-bad'
}

/** 写入剪贴板；不支持时返回 false，由调用方回退到手动复制弹窗。 */
export async function copyText(value: string) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value)
      ElMessage.success('已复制')
      return true
    } catch { return false }
  }
  return false
}

export async function fetchConfig(clashUrl: string) {
  const path = new URL(clashUrl).pathname
  const res = await fetch(path)
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(typeof data?.detail === 'string' ? data.detail : '配置获取失败')
  }
  return res.text()
}

export function downloadYaml(content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: 'application/yaml' }))
  const a = document.createElement('a')
  a.href = url
  a.download = 'ghelper.yaml'
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const strategyLabel: Record<string, string> = { auto: '最快优先', fixed: '指定节点', stable: '稳定优先', fallback: '主备优先' }

/** 链路的综合状态：停用 > 启动失败 > 最近检测失败 > 运行态。tone 对应样式与筛选分组。 */
export function chainState(row: Chain): { label: string, tone: 'off' | 'error' | 'warn' | 'ok' | 'idle' } {
  if (!row.enabled) return { label: '已停用', tone: 'off' }
  if (row.configuration_error || row.status.state === 'error') return { label: row.configuration_error ? '配置失效' : '启动失败', tone: 'error' }
  if (row.probe && !row.probe.reachable) return { label: '检测失败', tone: 'warn' }
  if (!row.relay_enabled) return { label: '仅订阅分发', tone: 'idle' }
  if (row.status.state === 'running') return { label: '运行中', tone: 'ok' }
  return { label: '等待启动', tone: 'idle' }
}

function bracketHost(host: string) {
  return host.includes(':') && !host.startsWith('[') ? '[' + host + ']' : host
}

export function relayAddress(row: Chain, settings: { proxy_public_host: string, public_base_url: string }) {
  let host = row.public_host || settings.proxy_public_host
  if (!host) host = hostOf(settings.public_base_url) || '系统域名'
  return bracketHost(host) + ':' + row.listen_port
}

export function connectionFields(c: ConnectionInfo) {
  const address = bracketHost(c.host) + ':' + c.port
  const auth = encodeURIComponent(c.username) + ':' + encodeURIComponent(c.password)
  const maskedAuth = encodeURIComponent(c.username) + ':••••••••'
  return [
    { label: '地址', value: address, masked: address },
    { label: '账号', value: c.username, masked: c.username },
    { label: '密码', value: c.password, masked: '••••••••' },
    { label: 'HTTP URI', value: 'http://' + auth + '@' + address, masked: 'http://' + maskedAuth + '@' + address },
    { label: 'SOCKS5 URI', value: 'socks5://' + auth + '@' + address, masked: 'socks5://' + maskedAuth + '@' + address }
  ]
}

export function connectionText(c: ConnectionInfo) {
  return connectionFields(c).map(f => f.label + '：' + f.value).join('\n')
}
