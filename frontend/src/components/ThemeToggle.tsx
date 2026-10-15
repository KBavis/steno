import { Monitor, Moon, Sun, type LucideIcon } from 'lucide-react'
import { setThemeChoice, useTheme, type ThemeChoice } from '../lib/theme'

const CHOICES: [ThemeChoice, string, LucideIcon][] = [
  ['light', 'Light', Sun],
  ['dark', 'Dark', Moon],
  ['system', 'Match system', Monitor],
]

/** Light, dark, or follow the OS */
export function ThemeToggle({ className = '' }: { className?: string }) {
  const { choice } = useTheme()
  return (
    <div className={`theme-toggle ${className}`} role="radiogroup" aria-label="Theme">
      {CHOICES.map(([value, label, Icon]) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={choice === value}
          className={choice === value ? 'on' : ''}
          title={label}
          aria-label={label}
          onClick={() => setThemeChoice(value)}
        >
          <Icon size={14} />
        </button>
      ))}
    </div>
  )
}
