import { Pencil, Trash2 } from 'lucide-react'
import type { ReactNode } from 'react'

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    // The hint sits outside the <label> so it isn't read as part of the field's name
    <div className="field">
      <label className="field">
        <span className="field-label">{label}</span>
        {children}
      </label>
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  )
}

/** Cancel + submit for a drawer footer. The submit button targets the form by id. */
export function DrawerActions({
  formId,
  editing,
  busy,
  onCancel,
  createLabel = 'Create',
}: {
  formId: string
  editing: boolean
  busy: boolean
  onCancel: () => void
  createLabel?: string
}) {
  return (
    <>
      <button type="button" className="btn btn-secondary" onClick={onCancel}>
        Cancel
      </button>
      <button type="submit" form={formId} className="btn btn-primary" disabled={busy}>
        {editing ? 'Save changes' : createLabel}
      </button>
    </>
  )
}

/** Edit and delete icon buttons, shown when the row is hovered. */
export function RowActions({ label, onEdit, onDelete }: { label: string; onEdit: () => void; onDelete: () => void }) {
  return (
    <>
      <button className="btn btn-ghost btn-icon reveal" onClick={onEdit} aria-label={`Edit ${label}`} title="Edit">
        <Pencil size={15} />
      </button>
      <button className="btn btn-danger btn-icon reveal" onClick={onDelete} aria-label={`Delete ${label}`} title="Delete">
        <Trash2 size={15} />
      </button>
    </>
  )
}
