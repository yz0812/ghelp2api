import { ElMessage } from 'element-plus'
import type { SubNode } from './types'

export const stateLabel: Record<string, string> = {
  pending: '等待获取', ready: '已就绪', error: '获取失败', invalid: '凭证失效',
  running: '进行中', completed: '已完成', partial: '部分失败', interrupted: '已中断'
}

export const jobKind: Record<string, string> = {
  refresh: '账号刷新', probe: '节点测速', sub_probe: '订阅测速', source: '订阅源检测'
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
    await navigator.clipboard.writeText(value)
    ElMessage.success('已复制')
    return true
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
