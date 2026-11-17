/** 地区识别由后端负责；前端统一去掉旧 emoji 并使用本地 SVG。 */
const flags = import.meta.glob<string>('./assets/flags/*.svg', { eager: true, query: '?url', import: 'default' })

/** 兼容旧接口：从原始地区标签的国旗字符还原国家码，不重复维护地名映射。 */
export function regionCountry(country?: string, label?: string) {
  const code = (country || '').trim().toUpperCase()
  if (flagUrl(code)) return code
  const pair = (label || '').match(/[\u{1F1E6}-\u{1F1FF}]{2}/u)?.[0]
  if (pair) {
    const legacy = Array.from(pair, ch => String.fromCharCode(ch.codePointAt(0)! - 0x1F1E6 + 65)).join('')
    if (flagUrl(legacy)) return legacy
  }
  return 'UN'
}

export function flagUrl(country?: string) {
  const code = (country || '').trim().toLowerCase()
  return /^[a-z]{2}$/.test(code) ? flags[`./assets/flags/${code}.svg`] : undefined
}

export function regionName(label?: string) {
  return (label || '').replace(/[\u{1F1E6}-\u{1F1FF}]/gu, '').trim() || '未知地区'
}
