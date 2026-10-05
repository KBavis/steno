import { FolderGit2, GitBranch, Play, Plus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api, type Connector, type Repository, type RepositoryInput } from '../api/client'
import { useAction } from '../hooks/useAction'
import { usePoll } from '../hooks/usePoll'
import { scopeValue } from '../lib/connectors'
import { spaceTree } from '../lib/spaces'
import { DrawerActions, Field, RowActions } from './Form'
import { KindChip } from './KindChip'
import { Drawer, EmptyState, ErrorAlert, PageTop } from './ui'

const EMPTY: RepositoryInput = {
  connector_id: 0,
  space_id: null,
  name: '',
  clone_url: '',
  default_branch: 'main',
}
const FORM_ID = 'repository-form'

/** The usual clone URL for a repository on this connector, e.g. https://github.com/acme/my-service.git */
function suggestCloneUrl(connector: Connector | undefined, name: string): string {
  const scope = connector && scopeValue(connector)
  if (!connector || !scope || !name) return ''
  const base = connector.base_url.replace(/\/+$/, '')
  return connector.kind === 'bitbucket' ? `${base}/scm/${scope.toLowerCase()}/${name}.git` : `${base}/${scope}/${name}.git`
}

/**
 * Repositories to ingest. Used on the Ingestion page (with Dry run buttons, when
 * `onJobQueued` is given) and in onboarding (`embedded`, without them).
 */
export function RepositoriesSection({
  onNavigate,
  onJobQueued,
  embedded,
}: {
  /** Links to other tabs ("add a connector first"); omitted in onboarding */
  onNavigate?: (tab: string) => void
  /** Shows a Dry run button per repository; called after a job is queued */
  onJobQueued?: () => void
  embedded?: boolean
}) {
  const repos = usePoll(api.repositories.list, 10000)
  const connectors = usePoll(api.connectors.list, 10000)
  const spaces = usePoll(api.spaces.list, 10000)

  // undefined: drawer closed; null: creating; Repository: editing it
  const [editing, setEditing] = useState<Repository | null>()
  const [form, setForm] = useState<RepositoryInput>(EMPTY)
  const [urlTouched, setUrlTouched] = useState(false)
  const [queued, setQueued] = useState<number>()
  const action = useAction()

  const connectorList = connectors.data ?? []
  const connector = connectorList.find((c) => c.id === form.connector_id)
  const spaceNodes = spaceTree(spaces.data ?? [])
  const spacePath = (id: number | null) => spaceNodes.find((n) => n.space.id === id)?.path
  const connectorOf = (id: number) => connectorList.find((c) => c.id === id)
  const derivedUrl = suggestCloneUrl(connector, form.name)

  function open(repo: Repository | null) {
    setEditing(repo)
    // An existing URL that matches the derived one stays derived, so renames carry through
    setUrlTouched(!!repo && repo.clone_url !== suggestCloneUrl(connectorOf(repo.connector_id), repo.name))
    setForm(repo ? { ...repo } : { ...EMPTY, connector_id: connectorList[0]?.id ?? 0, space_id: spaceNodes[0]?.space.id ?? null })
  }

  // Keep the clone URL in step with the name and connector until the user edits it
  function update(patch: Partial<RepositoryInput>) {
    const next = { ...form, ...patch }
    if (!urlTouched && ('name' in patch || 'connector_id' in patch)) {
      next.clone_url = suggestCloneUrl(
        connectorList.find((c) => c.id === next.connector_id),
        next.name,
      )
    }
    setForm(next)
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const ok = await action.run(() => (editing ? api.repositories.update(editing.id, form) : api.repositories.create(form)))
    if (ok) {
      setEditing(undefined)
      repos.reload()
    }
  }

  async function remove(repo: Repository) {
    if (!confirm(`Delete "${repo.name}" and its jobs?`)) return
    if (await action.run(() => api.repositories.remove(repo.id))) {
      repos.reload()
      onJobQueued?.()
    }
  }

  async function dryRun(repo: Repository) {
    setQueued(repo.id)
    if (await action.run(() => api.createJob(repo.id, 'dry_run'))) onJobQueued?.()
    setQueued(undefined)
  }

  const noConnectors = connectors.data && connectorList.length === 0
  const addButton = (
    <button className="btn btn-primary" onClick={() => open(null)} disabled={noConnectors}>
      <Plus size={16} /> Add repository
    </button>
  )

  return (
    <>
      <PageTop
        embedded={embedded}
        title={embedded ? 'Repositories' : 'Ingestion'}
        description="The repositories Steno ingests. A dry run writes no LLM text and reports the time each stage takes."
        action={repos.data?.length ? addButton : null}
      />
      <ErrorAlert message={repos.error ?? (editing === undefined ? action.error : undefined)} />

      <div className="card">
        {repos.data?.length === 0 ? (
          noConnectors ? (
            <EmptyState
              icon={FolderGit2}
              title="Add a connector first"
              action={
                onNavigate && (
                  <button className="btn btn-secondary" onClick={() => onNavigate('connectors')}>
                    Go to connectors
                  </button>
                )
              }
            >
              Repositories are reached through a connector to their git host.
            </EmptyState>
          ) : (
            <EmptyState icon={FolderGit2} title="No repositories yet" action={addButton}>
              Pick a repository to ingest and place it in a space.
            </EmptyState>
          )
        ) : (
          <ul className="list">
            {repos.data?.map((r) => {
              const c = connectorOf(r.connector_id)
              return (
                <li key={r.id} className="list-row">
                  {c ? <KindChip kind={c.kind} /> : <div className="avatar" />}
                  <div className="list-main">
                    <div className="list-title">{r.name}</div>
                    <div className="list-sub">
                      {spacePath(r.space_id) ?? 'No space'} · {c?.name ?? '?'}
                    </div>
                  </div>
                  <span className="badge" title="Default branch">
                    <GitBranch size={12} /> {r.default_branch}
                  </span>
                  {r.last_ingested_sha ? (
                    <span className="badge badge-ok mono">{r.last_ingested_sha.slice(0, 8)}</span>
                  ) : (
                    <span className="badge">Not ingested</span>
                  )}
                  <div className="list-actions">
                    {onJobQueued && (
                      <button className="btn btn-secondary btn-sm" onClick={() => dryRun(r)} disabled={queued === r.id}>
                        <Play size={13} /> Dry run
                      </button>
                    )}
                    <RowActions label={r.name} onEdit={() => open(r)} onDelete={() => remove(r)} />
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </div>

      {editing !== undefined && (
        <Drawer
          title={editing ? `Edit ${editing.name}` : 'Add repository'}
          description="Which repository to ingest, and where it belongs."
          onClose={() => setEditing(undefined)}
          footer={<DrawerActions formId={FORM_ID} editing={!!editing} busy={action.busy} onCancel={() => setEditing(undefined)} createLabel="Add repository" />}
        >
          <form id={FORM_ID} className="stack" onSubmit={submit}>
            <Field label="Connector">
              <select value={form.connector_id} onChange={(e) => update({ connector_id: Number(e.target.value) })}>
                {connectorList.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Repository name">
              <input autoFocus required placeholder="my-service" value={form.name} onChange={(e) => update({ name: e.target.value })} />
            </Field>
            <Field label="Space" hint="Where this repository belongs in your organization.">
              <select value={form.space_id ?? ''} onChange={(e) => update({ space_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">None</option>
                {spaceNodes.map(({ space, path }) => (
                  <option key={space.id} value={space.id}>
                    {path}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="Clone URL"
              hint={
                !urlTouched && connector ? (
                  'Derived from the connector and name.'
                ) : derivedUrl && derivedUrl !== form.clone_url ? (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => {
                      setUrlTouched(false)
                      setForm({ ...form, clone_url: derivedUrl })
                    }}
                  >
                    Use derived URL
                  </button>
                ) : undefined
              }
            >
              <input
                required
                className="mono"
                placeholder={derivedUrl || suggestCloneUrl(connector, 'my-service')}
                value={form.clone_url}
                onChange={(e) => {
                  // Clearing the field goes back to deriving it
                  const value = e.target.value
                  setUrlTouched(value !== '')
                  setForm({ ...form, clone_url: value || derivedUrl })
                }}
              />
            </Field>
            <Field label="Default branch">
              <input required value={form.default_branch} onChange={(e) => setForm({ ...form, default_branch: e.target.value })} />
            </Field>
            <ErrorAlert message={action.error} />
          </form>
        </Drawer>
      )}
    </>
  )
}
