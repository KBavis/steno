import { ArrowLeft, ArrowRight, Check } from 'lucide-react'
import { useState } from 'react'
import { api, type Organization } from '../api/client'
import { OrganizationForm } from '../components/OrganizationForm'
import { RepositoriesSection } from '../components/RepositoriesSection'
import { ErrorAlert, Logo } from '../components/ui'
import { useAction } from '../hooks/useAction'
import { usePoll } from '../hooks/usePoll'
import { ConnectorsPage } from './ConnectorsPage'
import { SpacesPage } from './SpacesPage'

const STEPS = [
  {
    title: 'Organization',
    heading: 'Tell us about your organization',
    blurb: 'It becomes the root of the knowledge graph, above every space.',
  },
  {
    title: 'Spaces',
    heading: 'How is your organization divided?',
    blurb: 'Teams, domains, product areas: whatever you call them. Spaces can nest, and every repository will belong to one.',
  },
  {
    title: 'Connectors',
    heading: 'Connect your git hosts',
    blurb: "A connector is a host plus what Steno may see there. You'll pick specific repositories next.",
  },
  {
    title: 'Repositories',
    heading: 'Choose what to ingest',
    blurb: 'Pick repositories and place each in a space. You can add more at any time.',
  },
] as const

/**
 * First-run flow: organization → spaces → connectors → repositories.
 * Shown until the admin finishes it (organization.onboarded_at is set).
 */
export function OnboardingPage({
  organization,
  onOrganizationSaved,
  onFinished,
}: {
  organization: Organization | null
  onOrganizationSaved: (org: Organization) => void
  onFinished: (org: Organization) => void
}) {
  const [step, setStep] = useState(organization ? 1 : 0)
  const spaces = usePoll(api.spaces.list, 3000)
  const connectors = usePoll(api.connectors.list, 3000)
  const finish = useAction()

  // What each step needs before moving on
  const ready = [
    organization !== null,
    (spaces.data?.length ?? 0) > 0,
    (connectors.data?.length ?? 0) > 0,
    true, // repositories are optional; they can be added after onboarding
  ]
  const missing = ['', 'Create at least one space to continue.', 'Create at least one connector to continue.', '']
  const last = step === STEPS.length - 1

  async function complete() {
    let org: Organization | undefined
    if (await finish.run(async () => (org = await api.organization.completeOnboarding()))) onFinished(org!)
  }

  return (
    <div className="onboarding">
      <div className="onboarding-inner">
        <div className="onboarding-brand">
          <Logo size={32} />
          <span className="brand-name" style={{ fontSize: 18 }}>
            Steno
          </span>
        </div>

        <ol className="stepper">
          {STEPS.map((s, i) => (
            <li key={s.title} className={i === step ? 'current' : i < step ? 'done' : ''}>
              <button disabled={i > step && !ready.slice(0, i).every(Boolean)} onClick={() => setStep(i)}>
                <span className="num">{i < step ? <Check size={12} strokeWidth={3} /> : i + 1}</span>
                {s.title}
              </button>
            </li>
          ))}
        </ol>

        <div className="onboarding-title">
          <h1>{STEPS[step].heading}</h1>
          <p>{STEPS[step].blurb}</p>
        </div>

        {step === 0 && (
          <div className="card card-body narrow">
            <OrganizationForm
              organization={organization}
              submitLabel={organization ? 'Save and continue' : 'Continue'}
              onSaved={(org) => {
                onOrganizationSaved(org)
                setStep(1)
              }}
            />
          </div>
        )}
        {step === 1 && <SpacesPage embedded onChange={spaces.reload} />}
        {step === 2 && <ConnectorsPage embedded onChange={connectors.reload} />}
        {step === 3 && <RepositoriesSection embedded />}

        {step > 0 && (
          <div className="onboarding-nav">
            <button className="btn btn-secondary" onClick={() => setStep(step - 1)}>
              <ArrowLeft size={15} /> Back
            </button>
            <span className="spacer">{!ready[step] && missing[step]}</span>
            {last ? (
              <button className="btn btn-primary" onClick={complete} disabled={finish.busy}>
                Finish setup <Check size={15} />
              </button>
            ) : (
              <button className="btn btn-primary" onClick={() => setStep(step + 1)} disabled={!ready[step]}>
                Next <ArrowRight size={15} />
              </button>
            )}
          </div>
        )}
        <ErrorAlert message={finish.error} />
      </div>
    </div>
  )
}
