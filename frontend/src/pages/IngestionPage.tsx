import { api } from '../api/client'
import { Jobs } from '../components/Jobs'
import { RepositoriesSection } from '../components/RepositoriesSection'
import { usePoll } from '../hooks/usePoll'

export function IngestionPage({ onNavigate }: { onNavigate: (tab: string) => void }) {
  const jobs = usePoll(api.jobs)
  const repos = usePoll(api.repositories.list, 10000)
  const repoName = (id: number) => repos.data?.find((r) => r.id === id)?.name ?? `repository ${id}`

  return (
    <>
      <RepositoriesSection
        onNavigate={onNavigate}
        onJobQueued={() => {
          jobs.reload()
          repos.reload()
        }}
      />
      <Jobs jobs={jobs.data} error={jobs.error} repoName={repoName} />
    </>
  )
}
