import { useEffect, useState, type FormEvent } from 'react'
import { api, type OrganizationSettings } from '../api/client'
import { Field } from '../components/Form'
import { ErrorAlert, PageHeader } from '../components/ui'
import { useAction } from '../hooks/useAction'

/** What the organization chooses for its deployment. Onboarding will ask these later. */
export function SettingsPage() {
  const [settings, setSettings] = useState<OrganizationSettings>()
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [saved, setSaved] = useState(false)
  const [loadError, setLoadError] = useState<string>()
  const save = useAction()

  useEffect(() => {
    api.organization
      .settings()
      .then((s) => {
        setSettings(s)
        setProvider(s.llm.provider)
        setModel(s.llm.model)
      })
      .catch((e: Error) => setLoadError(e.message))
  }, [])

  const options = settings?.llm_options ?? []
  const current = options.find((p) => p.id === provider)
  const changed = settings && (provider !== settings.llm.provider || model !== settings.llm.model)

  function pickProvider(id: string) {
    setProvider(id)
    const next = options.find((p) => p.id === id)
    if (next && !next.models.some((m) => m.id === model)) setModel(next.models[0]?.id ?? '')
    setSaved(false)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const ok = await save.run(async () => setSettings(await api.organization.putSettings({ llm: { provider, model } })))
    setSaved(ok)
  }

  return (
    <>
      <PageHeader
        title="Settings"
        description="What your organization chooses for this Steno deployment. Onboarding will ask these later."
      />
      <ErrorAlert message={loadError} />
      {settings && (
        <>
          <div className="section-title">
            <h2>Generated summaries</h2>
            <span className="muted small">
              {settings.llm.source === 'default' ? "using the deployment's default" : 'chosen by your organization'}
            </span>
          </div>
          <div className="card card-body" style={{ maxWidth: 560 }}>
            <p className="muted small" style={{ marginTop: 0 }}>
              An LLM writes a short summary of every application, space, and the organization, from what Steno found
              in the code. Routing reads them to find which space and application a question is about. Flows aren't
              summarized by an LLM: each has a digest built from its code.
            </p>
            <form className="stack" onSubmit={submit}>
              <Field label="Provider" hint={current?.hint}>
                <select value={provider} onChange={(e) => pickProvider(e.target.value)}>
                  {options.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field
                label="Model"
                hint="A summary is rewritten only when what it's written from changes, so a new model applies to summaries as their applications change."
              >
                <select
                  value={model}
                  onChange={(e) => {
                    setModel(e.target.value)
                    setSaved(false)
                  }}
                >
                  {(current?.models ?? []).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </Field>
              <ErrorAlert message={save.error} />
              <div>
                <button type="submit" className="btn btn-primary" disabled={save.busy || (!changed && settings.llm.source === 'organization')}>
                  Save
                </button>
                {saved && !changed && <span className="muted small" style={{ marginLeft: 12 }}>Saved.</span>}
              </div>
            </form>
          </div>
        </>
      )}
    </>
  )
}
