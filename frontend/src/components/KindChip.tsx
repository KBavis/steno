import type { ConnectorKind } from '../api/client'
import { KINDS } from '../lib/connectors'

/** A small colored monogram for a git host (GH, BB, GL). */
export function KindChip({ kind }: { kind: ConnectorKind }) {
  return (
    <span className={`kind-chip kind-${kind}`} title={KINDS[kind].label}>
      {KINDS[kind].short}
    </span>
  )
}
