import { Layers, Plus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { api, type Space, type SpaceInput } from '../api/client'
import { DrawerActions, Field, RowActions } from '../components/Form'
import { Drawer, EmptyState, ErrorAlert, PageTop } from '../components/ui'
import { useAction } from '../hooks/useAction'
import { usePoll } from '../hooks/usePoll'
import { selfAndDescendants, spaceTree } from '../lib/spaces'

const EMPTY: SpaceInput = { name: '', description: null, parent_id: null }
const FORM_ID = 'space-form'

/**
 * Admin-declared spaces as a tree. `onChange` runs after any create, edit, or delete
 * (onboarding uses it to enable Next); `embedded` drops the large page header.
 */
export function SpacesPage({ onChange, embedded }: { onChange?: () => void; embedded?: boolean }) {
  const spaces = usePoll(api.spaces.list, 10000)
  const repos = usePoll(api.repositories.list, 10000)
  // undefined: drawer closed; null: creating; Space: editing it
  const [editing, setEditing] = useState<Space | null>()
  const [form, setForm] = useState<SpaceInput>(EMPTY)
  const action = useAction()

  const all = spaces.data ?? []
  const blocked = editing ? selfAndDescendants(all, editing.id) : new Set<number>()
  const repoCount = (id: number) => repos.data?.filter((r) => r.space_id === id).length ?? 0

  function open(space: Space | null, parentId: number | null = null) {
    setEditing(space)
    setForm(space ? { name: space.name, description: space.description, parent_id: space.parent_id } : { ...EMPTY, parent_id: parentId })
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    const body = { ...form, description: form.description || null }
    const ok = await action.run(() => (editing ? api.spaces.update(editing.id, body) : api.spaces.create(body)))
    if (ok) {
      setEditing(undefined)
      spaces.reload()
      onChange?.()
    }
  }

  async function remove(space: Space) {
    if (!confirm(`Delete "${space.name}" and its child spaces? Its repositories stay, unassigned.`)) return
    if (await action.run(() => api.spaces.remove(space.id))) {
      spaces.reload()
      repos.reload()
      onChange?.()
    }
  }

  const addButton = (
    <button className="btn btn-primary" onClick={() => open(null)}>
      <Plus size={16} /> New space
    </button>
  )

  return (
    <>
      <PageTop
        embedded={embedded}
        title="Spaces"
        description="How your organization divides itself: teams, domains, product areas. Spaces can nest, and every repository belongs to one."
        action={all.length > 0 && addButton}
      />
      <ErrorAlert message={spaces.error ?? (editing === undefined ? action.error : undefined)} />

      <div className="card">
        {spaces.data && all.length === 0 ? (
          <EmptyState icon={Layers} title="No spaces yet" action={addButton}>
            Start with the top-level areas of your organization. You can nest spaces inside them later.
          </EmptyState>
        ) : (
          <ul className="list">
            {spaceTree(all).map(({ space, depth }) => (
              <li key={space.id} className="list-row">
                {depth > 0 && (
                  <>
                    <span style={{ width: (depth - 1) * 22 }} />
                    <span className="tree-indent" />
                  </>
                )}
                <div className="avatar">
                  <Layers size={16} />
                </div>
                <div className="list-main">
                  <div className="list-title">{space.name}</div>
                  <div className="list-sub">{space.description || 'No description'}</div>
                </div>
                <span className="badge">
                  {repoCount(space.id)} {repoCount(space.id) === 1 ? 'repository' : 'repositories'}
                </span>
                <div className="list-actions">
                  <button className="btn btn-ghost btn-sm reveal" onClick={() => open(null, space.id)} title="Add a child space">
                    <Plus size={14} /> Child
                  </button>
                  <RowActions label={space.name} onEdit={() => open(space)} onDelete={() => remove(space)} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {editing !== undefined && (
        <Drawer
          title={editing ? `Edit ${editing.name}` : 'New space'}
          description="Spaces are declared here and copied into the knowledge graph."
          onClose={() => setEditing(undefined)}
          footer={<DrawerActions formId={FORM_ID} editing={!!editing} busy={action.busy} onCancel={() => setEditing(undefined)} createLabel="Create space" />}
        >
          <form id={FORM_ID} className="stack" onSubmit={submit}>
            <Field label="Name">
              <input autoFocus required placeholder="Platform" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label="Description" hint="What this part of the organization is responsible for.">
              <textarea rows={3} value={form.description ?? ''} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </Field>
            <Field label="Parent space">
              <select
                value={form.parent_id ?? ''}
                onChange={(e) => setForm({ ...form, parent_id: e.target.value ? Number(e.target.value) : null })}
              >
                <option value="">None (top level)</option>
                {spaceTree(all)
                  .filter(({ space }) => !blocked.has(space.id))
                  .map(({ space, path }) => (
                    <option key={space.id} value={space.id}>
                      {path}
                    </option>
                  ))}
              </select>
            </Field>
            <ErrorAlert message={action.error} />
          </form>
        </Drawer>
      )}
    </>
  )
}
