import { Building2, FolderGit2, Layers, Network, Plug, ScanSearch, type LucideIcon } from 'lucide-react'
import { lazy, Suspense, useEffect, useState } from 'react'
import { api, type Organization } from './api/client'
import { HealthStatus } from './components/HealthStatus'
import { ErrorAlert, Logo } from './components/ui'
import { ConnectorsPage } from './pages/ConnectorsPage'
import { CoveragePage } from './pages/CoveragePage'
import { IngestionPage } from './pages/IngestionPage'
import { OnboardingPage } from './pages/OnboardingPage'
import { OrganizationPage } from './pages/OrganizationPage'
import { SpacesPage } from './pages/SpacesPage'

// The graph view pulls in React Flow and ELK; load it only when the tab opens
const VisualizePage = lazy(() => import('./pages/VisualizePage').then((m) => ({ default: m.VisualizePage })))

const TABS: Record<string, { label: string; icon: LucideIcon }> = {
  ingestion: { label: 'Ingestion', icon: FolderGit2 },
  visualize: { label: 'Visualize', icon: Network },
  coverage: { label: 'Coverage', icon: ScanSearch },
  spaces: { label: 'Spaces', icon: Layers },
  connectors: { label: 'Connectors', icon: Plug },
  organization: { label: 'Organization', icon: Building2 },
}

// The tab lives in the URL hash (#spaces), so reloads and links keep it.
// Visualize keeps its own location after a slash (#visualize/flow/<id>).
function tabFromHash(): string {
  const hash = window.location.hash.slice(1).split('/')[0]
  return hash in TABS ? hash : 'ingestion'
}

export default function App() {
  const [tab, setTab] = useState(tabFromHash)
  // undefined: still loading; null: no organization yet (onboarding)
  const [org, setOrg] = useState<Organization | null>()
  const [error, setError] = useState<string>()

  useEffect(() => {
    api.organization
      .get()
      .then(setOrg)
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(() => {
    const onHash = () => setTab(tabFromHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const navigate = (next: string) => {
    window.location.hash = next
  }

  if (error) {
    return (
      <div className="onboarding">
        <div className="onboarding-inner narrow">
          <ErrorAlert message={`Couldn't reach the Steno API: ${error}`} />
        </div>
      </div>
    )
  }
  if (org === undefined) return null
  if (!org?.onboarded_at) {
    return <OnboardingPage organization={org} onOrganizationSaved={setOrg} onFinished={setOrg} />
  }

  // The graph takes the whole window: it's a place to move around in, not a page
  if (tab === 'visualize') {
    return (
      <Suspense fallback={null}>
        <VisualizePage onExit={() => navigate('ingestion')} />
      </Suspense>
    )
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <Logo />
          <div>
            <div className="brand-name">Steno</div>
            <div className="brand-org">{org.name}</div>
          </div>
        </div>
        <nav className="nav">
          <div className="nav-label">Workspace</div>
          {Object.entries(TABS).map(([key, { label, icon: Icon }]) => (
            <a key={key} href={`#${key}`} className={tab === key ? 'active' : ''}>
              <Icon size={16} />
              {label}
            </a>
          ))}
        </nav>
        <div className="sidebar-footer">
          <HealthStatus />
        </div>
      </aside>
      <main className="content">
        {tab === 'ingestion' && <IngestionPage onNavigate={navigate} />}
        {tab === 'coverage' && <CoveragePage />}
        {tab === 'spaces' && <SpacesPage />}
        {tab === 'connectors' && <ConnectorsPage />}
        {tab === 'organization' && <OrganizationPage organization={org} onChanged={setOrg} />}
      </main>
    </div>
  )
}
