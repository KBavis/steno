import { useState, type FormEvent } from 'react'
import { api, type Organization } from '../api/client'
import { useAction } from '../hooks/useAction'
import { Field } from './Form'
import { ErrorAlert } from './ui'

/** Create or edit the organization (one per deployment). */
export function OrganizationForm({
  organization,
  submitLabel,
  onSaved,
}: {
  organization: Organization | null
  submitLabel: string
  onSaved: (org: Organization) => void
}) {
  const [name, setName] = useState(organization?.name ?? '')
  const [description, setDescription] = useState(organization?.description ?? '')
  const action = useAction()

  async function submit(e: FormEvent) {
    e.preventDefault()
    let saved: Organization | undefined
    const ok = await action.run(async () => {
      saved = await api.organization.put({ name, description: description || null })
    })
    if (ok && saved) onSaved(saved)
  }

  return (
    <form className="stack" onSubmit={submit}>
      <Field label="Organization name">
        <input autoFocus={!organization} required placeholder="Acme" value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Description" hint="What the organization does. Agents see it as the top of the map.">
        <textarea rows={3} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <ErrorAlert message={action.error} />
      <div>
        <button type="submit" className="btn btn-primary" disabled={action.busy}>
          {submitLabel}
        </button>
      </div>
    </form>
  )
}
