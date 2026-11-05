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
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKeyRef, setApiKeyRef] = useState('')
  const [check, setCheck] = useState<{ ok: boolean; detail: string }>()
  const [checking, setChecking] = useState(false)
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
        setBaseUrl(s.llm.base_url ?? '')
        setApiKeyRef(s.llm.api_key_ref ?? '')
      })
      .catch((e: Error) => setLoadError(e.message))
  }, [])

  const options = settings?.llm_options ?? []
  const current = options.find((p) => p.id === provider)
  const named = current ? current.models.length === 0 : false
  const changed =
    settings &&
    (provider !== settings.llm.provider ||
      model !== settings.llm.model ||
      baseUrl !== (settings.llm.base_url ?? '') ||
      apiKeyRef !== (settings.llm.api_key_ref ?? ''))

  function pickProvider(id: string) {
    setProvider(id)
    const next = options.find((p) => p.id === id)
    if (next && !next.models.some((m) => m.id === model)) setModel(next.models[0]?.id ?? '')
    if (next && !next.connection) {
      setBaseUrl('')
      setApiKeyRef('')
    }
    setSaved(false)
    setCheck(undefined)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const llm = { provider, model, base_url: baseUrl || null, api_key_ref: apiKeyRef || null }
    const ok = await save.run(async () => setSettings(await api.organization.putSettings({ llm })))
    setSaved(ok)
    setCheck(undefined)
  }

  async function test() {
    setChecking(true)
    setCheck(undefined)
    try {
      setCheck(await api.organization.checkLlm())
    } catch (e) {
      setCheck({ ok: false, detail: (e as Error).message })
    } finally {
      setChecking(false)
    }
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
            <h2>LLM</h2>
            <span className="muted small">
              {settings.llm.source === 'default' ? "using the deployment's default" : 'chosen by your organization'}
            </span>
          </div>
          <div className="card card-body" style={{ maxWidth: 560 }}>
            <p className="muted small" style={{ marginTop: 0 }}>
              Your organization's LLM. It writes a short summary of every application, space, and the organization, from what Steno found
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
                {named ? (
                  <input
                    value={model}
                    placeholder="openai/your-gateway-model"
                    onChange={(e) => {
                      setModel(e.target.value)
                      setSaved(false)
                    }}
                  />
                ) : (
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
                )}
              </Field>
              {current?.connection && (
                <>
                  <Field label="Address" hint="Your gateway's address. Leave it empty for a vendor LiteLLM reaches on its own.">
                    <input
                      value={baseUrl}
                      placeholder="https://llm-gateway.example.com/v1"
                      onChange={(e) => {
                        setBaseUrl(e.target.value)
                        setSaved(false)
                      }}
                    />
                  </Field>
                  <Field label="Key" hint="A reference to the key, never the key: env:NAME, with NAME set where the worker runs.">
                    <input
                      value={apiKeyRef}
                      placeholder="env:LLM_GATEWAY_KEY"
                      onChange={(e) => {
                        setApiKeyRef(e.target.value)
                        setSaved(false)
                      }}
                    />
                  </Field>
                </>
              )}
              <ErrorAlert message={save.error} />
              <div>
                <button type="submit" className="btn btn-primary" disabled={save.busy || (!changed && settings.llm.source === 'organization')}>
                  Save
                </button>
                <button type="button" className="btn btn-secondary" style={{ marginLeft: 8 }} disabled={checking || !!changed} onClick={test} title={changed ? 'Save first' : 'Send one short prompt'}>
                  {checking ? 'Testing…' : 'Test connection'}
                </button>
                {saved && !changed && <span className="muted small" style={{ marginLeft: 12 }}>Saved.</span>}
                {check && (
                  <p className={check.ok ? 'small' : 'small error-text'} style={{ marginBottom: 0 }}>
                    {check.ok ? 'It answered: ' : "It didn't answer: "}
                    {check.detail}
                  </p>
                )}
              </div>
            </form>
          </div>
        </>
      )}
    </>
  )
}
