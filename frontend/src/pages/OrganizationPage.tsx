import { useState } from 'react'
import { api, type Organization } from '../api/client'
import { OrganizationForm } from '../components/OrganizationForm'
import { PageHeader } from '../components/ui'
import { usePoll } from '../hooks/usePoll'

export function OrganizationPage({
  organization,
  onChanged,
}: {
  organization: Organization
  onChanged: (org: Organization) => void
}) {
  const [saved, setSaved] = useState(false)
  const spaces = usePoll(api.spaces.list, 10000)
  const connectors = usePoll(api.connectors.list, 10000)
  const repos = usePoll(api.repositories.list, 10000)

  const stats = [
    ['Spaces', spaces.data?.length],
    ['Connectors', connectors.data?.length],
    ['Repositories', repos.data?.length],
  ] as const

  return (
    <>
      <PageHeader
        title="Organization"
        description="The organization this Steno deployment serves: the root of the knowledge graph, above every space."
      />
      <div className="stats">
        {stats.map(([label, value]) => (
          <div key={label} className="card stat">
            <span className="muted small">{label}</span>
            <strong>{value ?? '–'}</strong>
          </div>
        ))}
      </div>
      <div className="card card-body" style={{ maxWidth: 560 }}>
        <OrganizationForm
          organization={organization}
          submitLabel="Save changes"
          onSaved={(org) => {
            setSaved(true)
            onChanged(org)
          }}
        />
        {saved && <p className="muted small">Saved.</p>}
        {organization.onboarded_at && (
          <p className="muted small" style={{ marginBottom: 0 }}>
            Onboarded {new Date(organization.onboarded_at).toLocaleDateString()}
          </p>
        )}
      </div>
    </>
  )
}
