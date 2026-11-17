import { computed, readonly, ref } from 'vue'
import type { ThemePreference } from '../types'

const key = 'ghelper.theme'
const media = window.matchMedia('(prefers-color-scheme: dark)')
const valid = (value: unknown): value is ThemePreference => ['light', 'dark', 'system'].includes(String(value))
const initial = document.documentElement.dataset.themePreference
const preference = ref<ThemePreference>(valid(initial) ? initial : 'system')
const systemDark = ref(media.matches)
const dark = computed(() => preference.value === 'dark' || (preference.value === 'system' && systemDark.value))

function apply() {
  const root = document.documentElement
  root.classList.toggle('dark', dark.value)
  root.dataset.themePreference = preference.value
  root.style.colorScheme = dark.value ? 'dark' : 'light'
  root.style.backgroundColor = dark.value ? '#11151d' : '#f5f7fb'
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', dark.value ? '#11151d' : '#f5f7fb')
}

function setTheme(value: ThemePreference) {
  preference.value = value
  try { localStorage.setItem(key, value) } catch { /* 禁用存储时仍可在当前页面切换。 */ }
  apply()
}

media.addEventListener('change', event => { systemDark.value = event.matches; apply() })
window.addEventListener('storage', event => {
  if (event.key !== key && event.key !== null) return
  preference.value = valid(event.newValue) ? event.newValue : 'system'
  apply()
})
apply()

export function useTheme() {
  return { preference: readonly(preference), dark, setTheme }
}
