import { useSyncExternalStore } from 'react'

// Light or dark, or follow the OS. The choice lives in localStorage; the resolved theme is
// set as <html data-theme="light|dark">, which the CSS keys off. index.html sets it once
// before the first paint so the page doesn't flash; this module keeps it up to date.

export type ThemeChoice = 'light' | 'dark' | 'system'
export type Theme = 'light' | 'dark'

const KEY = 'steno.theme'
const systemDark = window.matchMedia('(prefers-color-scheme: dark)')
const listeners = new Set<() => void>()

function readChoice(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch {
    return 'system'
  }
}

let choice = readChoice()

function resolve(): Theme {
  return choice === 'system' ? (systemDark.matches ? 'dark' : 'light') : choice
}

function apply() {
  document.documentElement.dataset.theme = resolve()
  listeners.forEach((l) => l())
}

systemDark.addEventListener('change', () => {
  if (choice === 'system') apply()
})

export function setThemeChoice(next: ThemeChoice) {
  choice = next
  try {
    if (next === 'system') localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, next)
  } catch {
    // storage blocked: the choice lasts for this page load
  }
  apply()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** The user's choice and the theme it resolves to */
export function useTheme(): { choice: ThemeChoice; theme: Theme } {
  const current = useSyncExternalStore(subscribe, () => choice)
  const theme = useSyncExternalStore(subscribe, resolve)
  return { choice: current, theme }
}

apply()
