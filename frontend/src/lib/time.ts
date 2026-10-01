const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 3600],
  ['month', 30 * 24 * 3600],
  ['day', 24 * 3600],
  ['hour', 3600],
  ['minute', 60],
]
const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

/** "just now", "5 minutes ago", "yesterday" */
export function timeAgo(iso: string): string {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return rtf.format(-Math.round(seconds / size), unit)
  }
  return 'just now'
}

/** Seconds between two timestamps, formatted: "0.4s", "12s", "3m 05s" */
export function duration(from: string | null, to: string | null): string | null {
  if (!from || !to) return null
  const s = (new Date(to).getTime() - new Date(from).getTime()) / 1000
  if (s < 10) return `${s.toFixed(1)}s`
  if (s < 60) return `${Math.round(s)}s`
  return `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, '0')}s`
}
