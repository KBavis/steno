import { X, type LucideIcon } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'

/** Steno's mark: stacked strata, after Nicolas Steno's law of superposition. */
export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M7 11.5 16 7l9 4.5-9 4.5z" fill="white" />
      <path d="m7 16 9 4.5 9-4.5" fill="none" stroke="white" strokeOpacity=".75" strokeWidth="2" strokeLinejoin="round" />
      <path d="m7 20.5 9 4.5 9-4.5" fill="none" stroke="white" strokeOpacity=".5" strokeWidth="2" strokeLinejoin="round" />
    </svg>
  )
}

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string
  description?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {action}
    </div>
  )
}

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
}: {
  icon: LucideIcon
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon size={22} />
      </div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  )
}

export function ErrorAlert({ message }: { message?: string }) {
  if (!message) return null
  return <div className="alert">{message}</div>
}

/** A panel that slides in from the right, for create and edit forms. Escape closes it. */
export function Drawer({
  title,
  description,
  onClose,
  footer,
  children,
}: {
  title: string
  description?: ReactNode
  onClose: () => void
  footer: ReactNode
  children: ReactNode
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label={title}>
        <div className="drawer-header">
          <div>
            <h2>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close">
            <X size={16} />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
        <div className="drawer-footer">{footer}</div>
      </aside>
    </>
  )
}

/**
 * A page's title row. Full pages get a large header; `embedded` (inside onboarding,
 * which has its own title) gets a compact one.
 */
export function PageTop({
  embedded,
  title,
  description,
  action,
}: {
  embedded?: boolean
  title: string
  description?: ReactNode
  action?: ReactNode
}) {
  if (!embedded) return <PageHeader title={title} description={description} action={action} />
  return (
    <div className="section-title" style={{ marginTop: 0 }}>
      <h2>{title}</h2>
      {action}
    </div>
  )
}
