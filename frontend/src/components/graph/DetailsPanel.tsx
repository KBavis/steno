import { ArrowUpRight, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api, type NodeDetail } from '../../api/client'
import { EDGE_TYPES, kindStyle } from '../../lib/graphStyle'

const DRILL: Record<string, string> = { application: 'Open application', flow: 'Open flow', space: 'Open space', organization: 'Open organization' }

// Incoming relationships that read better with their own word
const INVERSE: Record<string, string> = {
  'in:BELONGS_TO': 'contains',
  'in:EXPOSES': 'exposed by',
  'in:CALLS': 'called by',
  'in:READS_FROM': 'read by',
  'in:WRITES_TO': 'written by',
  'in:STARTS': 'started by',
  'in:ENTRY': 'entry of',
  'in:INVOKES': 'invoked by',
}

/** The selected node: what it is, where it came from in the code, and what it's connected to. */
export function DetailsPanel({
  id,
  onClose,
  onSelect,
  onOpen,
}: {
  id: string
  onClose: () => void
  onSelect: (id: string) => void
  onOpen: (kind: string, id: string) => void
}) {
  const [detail, setDetail] = useState<NodeDetail>()
  const [error, setError] = useState<string>()

  useEffect(() => {
    let live = true
    setDetail(undefined)
    setError(undefined)
    api.graph
      .node(id)
      .then((d) => live && setDetail(d))
      .catch((e: Error) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [id])

  const groups = useMemo(() => {
    const out = new Map<string, NodeDetail['neighbors']>()
    for (const n of detail?.neighbors ?? []) {
      if (n.type === 'BELONGS_TO' && n.dir === 'out') continue // shown as the breadcrumb
      const key = `${n.dir}:${n.type}`
      out.set(key, [...(out.get(key) ?? []), n])
    }
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [detail])

  if (error) return <aside className="gpanel"><div className="alert">{error}</div></aside>
  if (!detail) return <aside className="gpanel"><div className="gpanel-loading">Loading…</div></aside>

  const style = kindStyle(detail.kind)
  const Icon = style.icon
  const props = Object.entries(detail.properties).filter(([, v]) => v !== null && v !== '' && typeof v !== 'object')
  const listProps = Object.entries(detail.properties).filter(([, v]) => Array.isArray(v) && (v as unknown[]).length)
  const prov = detail.provenance

  return (
    <aside className="gpanel" aria-label="Node details">
      <div className="gpanel-head">
        <span className={`gpanel-icon tone-${style.tone}`}><Icon size={16} /></span>
        <div className="gpanel-title">
          <div className="gpanel-kind">{style.label}{detail.labels.includes('Service') ? ' · Service' : ''}</div>
          <h3>{detail.label}</h3>
        </div>
        <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close details"><X size={16} /></button>
      </div>

      <div className="gpanel-actions">
        {DRILL[detail.kind] && (
          <button className="btn btn-primary btn-sm" onClick={() => onOpen(detail.kind, detail.id)}>{DRILL[detail.kind]}</button>
        )}
        {detail.source_url && (
          <a className="btn btn-secondary btn-sm" href={detail.source_url} target="_blank" rel="noreferrer">
            View source <ArrowUpRight size={13} />
          </a>
        )}
      </div>

      {prov.source_file && (
        <div className="gpanel-source">
          <span className="mono">{String(prov.source_file)}{prov.source_line ? `:${prov.source_line}` : ''}</span>
          <span className="muted small">
            {prov.extracted_by ? `found by ${prov.extracted_by}` : ''}
            {prov.commit ? ` · ${String(prov.commit).slice(0, 7)}` : ''}
          </span>
        </div>
      )}

      {props.length > 0 && (
        <dl className="gprops">
          {props.map(([k, v]) => (
            <div key={k}><dt>{k.replace(/_/g, ' ')}</dt><dd className="mono">{String(v)}</dd></div>
          ))}
          {listProps.map(([k, v]) => (
            <div key={k}><dt>{k}</dt><dd className="mono">{(v as unknown[]).join(', ')}</dd></div>
          ))}
        </dl>
      )}

      <div className="gpanel-section">Connections</div>
      {groups.length === 0 && <p className="muted small">None.</p>}
      {groups.map(([key, items]) => {
        const [dir, type] = key.split(':')
        const verb = EDGE_TYPES[type]?.label ?? type.toLowerCase().replace(/_/g, ' ')
        const heading = INVERSE[key] ?? (dir === 'out' ? verb : `${verb} by`)
        return (
          <div key={key} className="gconn">
            <div className="gconn-head">
              <span className={`edge-chip e-${type.toLowerCase()}`}>{heading}</span>
              <span className="muted small">{items.length}</span>
            </div>
            <ul>
              {items.slice(0, 40).map((n, i) => {
                const s = kindStyle(n.node.kind)
                const NIcon = s.icon
                return (
                  <li key={`${n.node.id}-${i}`}>
                    <button className="gconn-item" onClick={() => onSelect(n.node.id)}>
                      <NIcon size={13} className={`tone-text-${s.tone}`} />
                      <span>{n.node.label}</span>
                      {typeof n.props.operation === 'string' && <span className="muted small">{n.props.operation}</span>}
                      {n.props.async === true && <span className="muted small">async</span>}
                    </button>
                  </li>
                )
              })}
              {items.length > 40 && <li className="muted small">…and {items.length - 40} more</li>}
            </ul>
          </div>
        )
      })}
    </aside>
  )
}
