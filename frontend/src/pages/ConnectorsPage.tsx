import { KeyRound, Plug, Plus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api, type Connector, type ConnectorInput, type ConnectorKind } from '../api/client'
import { DrawerActions, Field, RowActions } from '../components/Form'
import { KindChip } from '../components/KindChip'
import { Drawer, EmptyState, ErrorAlert, PageTop } from '../components/ui'
import { useAction } from '../hooks/useAction'
import { usePoll } from '../hooks/usePoll'
import { KINDS, connectorLocation, scopeValue } from '../lib/connectors'

const EMPTY: ConnectorInput = {
  name: '',
  kind: 'github',
  base_url: KINDS.github.baseUrl,
  scope: {},
  credentials_ref: null,
}
const FORM_ID = 'connector-form'

/**
 * Git hosts and what Steno may see on each. `onChange` runs after any create, edit, or
 * delete (onboarding uses it to enable Next); `embedded` drops the large page header.
 */
export function ConnectorsPage({ onChange, embedded }: { onChange?: () => void; embedded?: boolean }) {
  const connectors = usePoll(api.connectors.list, 10000)
  const repos = usePoll(api.repositories.list, 10000)
  // undefined: drawer closed; null: creating; Connector: editing it
  const [editing, setEditing] = useState<Connector | null>()
  const [form, setForm] = useState<ConnectorInput>(EMPTY)
  const action = useAction()
  const kind = KINDS[form.kind]
  const repoCount = (id: number) => repos.data?.filter((r) => r.connector_id === id).length ?? 0

  function open(connector: Connector | null) {
    setEditing(connector)
    setForm(connector ? { ...connector } : EMPTY)
  }

  function setKind(next: ConnectorKind) {
    // Swap the default base URL unless the user typed their own
    const keepUrl = form.base_url && form.base_url !== kind.baseUrl
    setForm({ ...form, kind: next, base_url: keepUrl ? form.base_url : KINDS[next].baseUrl, scope: {} })
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const body = { ...form, credentials_ref: form.credentials_ref || null }
    const ok = await action.run(() => (editing ? api.connectors.update(editing.id, body) : api.connectors.create(body)))
    if (ok) {
      setEditing(undefined)
      connectors.reload()
      onChange?.()
    }
  }

  async function remove(connector: Connector) {
    if (!confirm(`Delete "${connector.name}"? Its repositories and their jobs are deleted too.`)) return
    if (await action.run(() => api.connectors.remove(connector.id))) {
      connectors.reload()
      onChange?.()
    }
  }

  const addButton = (
    <button className="btn btn-primary" onClick={() => open(null)}>
      <Plus size={16} /> New connector
    </button>
  )

  return (
    <>
      <PageTop
        embedded={embedded}
        title="Connectors"
        description="The git hosts Steno reads from, and what it may see on each. You choose which repositories to ingest separately."
        action={connectors.data?.length ? addButton : null}
      />
      <ErrorAlert message={connectors.error ?? (editing === undefined ? action.error : undefined)} />

      <div className="card">
        {connectors.data?.length === 0 ? (
          <EmptyState icon={Plug} title="No connectors yet" action={addButton}>
            Connect GitHub, Bitbucket, or GitLab. Public repositories need no credentials.
          </EmptyState>
        ) : (
          <ul className="list">
            {connectors.data?.map((c) => (
              <li key={c.id} className="list-row">
                <KindChip kind={c.kind} />
                <div className="list-main">
                  <div className="list-title">{c.name}</div>
                  <div className="list-sub">{connectorLocation(c)}</div>
                </div>
                {c.credentials_ref ? (
                  <span className="badge" title={c.credentials_ref}>
                    <KeyRound size={12} /> {c.credentials_ref}
                  </span>
                ) : (
                  <span className="badge">Public access</span>
                )}
                <span className="badge">
                  {repoCount(c.id)} {repoCount(c.id) === 1 ? 'repository' : 'repositories'}
                </span>
                <div className="list-actions">
                  <RowActions label={c.name} onEdit={() => open(c)} onDelete={() => remove(c)} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {editing !== undefined && (
        <Drawer
          title={editing ? `Edit ${editing.name}` : 'New connector'}
          description="A git host plus the part of it Steno may read."
          onClose={() => setEditing(undefined)}
          footer={<DrawerActions formId={FORM_ID} editing={!!editing} busy={action.busy} onCancel={() => setEditing(undefined)} createLabel="Create connector" />}
        >
          <form id={FORM_ID} className="stack" onSubmit={submit}>
            <Field label="Host">
              <div className="segmented" role="radiogroup">
                {(Object.keys(KINDS) as ConnectorKind[]).map((k) => (
                  <button
                    key={k}
                    type="button"
                    role="radio"
                    aria-checked={form.kind === k}
                    className={form.kind === k ? 'active' : ''}
                    onClick={() => setKind(k)}
                  >
                    <KindChip kind={k} /> {KINDS[k].label}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Name" hint="How this connector is labeled in Steno.">
              <input autoFocus required placeholder={form.kind} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Base URL">
              <input
                required
                placeholder="https://bitbucket.example.com"
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              />
            </Field>
            <Field label={kind.scopeLabel} hint="The scope: the part of the host this connector covers.">
              <input
                placeholder={kind.placeholder}
                value={scopeValue(form)}
                onChange={(e) => setForm({ ...form, scope: e.target.value ? { [kind.scopeKey]: e.target.value } : {} })}
              />
            </Field>
            <Field label="Credentials reference" hint="Where the token lives, never the token itself. Leave empty for public repositories.">
              <input
                className="mono"
                placeholder="env:STENO_GITHUB_TOKEN"
                value={form.credentials_ref ?? ''}
                onChange={(e) => setForm({ ...form, credentials_ref: e.target.value })}
              />
            </Field>
            <ErrorAlert message={action.error} />
          </form>
        </Drawer>
      )}
    </>
  )
}
