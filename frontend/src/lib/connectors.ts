import type { Connector, ConnectorKind } from '../api/client'

/** How each host names the part of it a connector covers (its scope). */
export const KINDS: Record<
  ConnectorKind,
  { label: string; short: string; baseUrl: string; scopeKey: string; scopeLabel: string; placeholder: string }
> = {
  github: {
    label: 'GitHub',
    short: 'GH',
    baseUrl: 'https://github.com',
    scopeKey: 'org',
    scopeLabel: 'Organization or user',
    placeholder: 'KBavis',
  },
  bitbucket: {
    label: 'Bitbucket',
    short: 'BB',
    baseUrl: '',
    scopeKey: 'project',
    scopeLabel: 'Project key',
    placeholder: 'PAY',
  },
  gitlab: {
    label: 'GitLab',
    short: 'GL',
    baseUrl: 'https://gitlab.com',
    scopeKey: 'group',
    scopeLabel: 'Group',
    placeholder: 'my-group/backend',
  },
}

export function scopeValue(c: Pick<Connector, 'kind' | 'scope'>): string {
  return c.scope[KINDS[c.kind].scopeKey] ?? ''
}

/** "github.com/KBavis": where the connector points, for display */
export function connectorLocation(c: Pick<Connector, 'kind' | 'scope' | 'base_url'>): string {
  const host = c.base_url.replace(/^https?:\/\//, '').replace(/\/+$/, '')
  const scope = scopeValue(c)
  return scope ? `${host}/${scope}` : host
}
