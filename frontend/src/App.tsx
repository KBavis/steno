import { api } from './api/client'
import { HealthBadge } from './components/HealthBadge'
import { Jobs } from './components/Jobs'
import { Repositories } from './components/Repositories'
import { usePoll } from './hooks/usePoll'

export default function App() {
  const jobs = usePoll(api.jobs)

  return (
    <div className="app">
      <header>
        <h1>Steno</h1>
        <HealthBadge />
      </header>
      <main>
        <Repositories onQueued={jobs.reload} />
        <Jobs jobs={jobs.data} error={jobs.error} />
      </main>
    </div>
  )
}
