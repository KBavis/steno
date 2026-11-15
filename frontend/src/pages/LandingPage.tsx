import {
  ArrowRight,
  BookCheck,
  Boxes,
  Cable,
  Compass,
  Eye,
  FileSearch,
  GitBranch,
  GitPullRequest,
  Lock,
  Network,
  Quote,
  Route,
  ScrollText,
  Waypoints,
  type LucideIcon,
} from 'lucide-react'
import { ThemeToggle } from '../components/ThemeToggle'
import { Logo } from '../components/ui'

type Pillar = { icon: LucideIcon; name: string; question: string; body: string; stage: string; live: boolean }

const PILLARS: Pillar[] = [
  {
    icon: Network,
    name: 'Architecture',
    question: 'What is there, and how does it connect?',
    body: 'Every application, the endpoints and topics it exposes and calls, and the flows from each entry point to every service it reaches.',
    stage: 'Proof of concept',
    live: true,
  },
  {
    icon: GitBranch,
    name: 'Projects',
    question: "What's changing, and why?",
    body: 'Work in flight and work already done, traced from tickets and pull requests to the services, teams, and people behind it.',
    stage: 'Next',
    live: false,
  },
  {
    icon: BookCheck,
    name: 'Constraints',
    question: 'What must be respected?',
    body: "The organization's standards, conventions, and rules, attached to the parts of the system they apply to.",
    stage: 'Next',
    live: false,
  },
]

const STEPS: { icon: LucideIcon; name: string; body: string }[] = [
  { icon: FileSearch, name: 'Read', body: 'Clone each repository and read its code and configuration.' },
  { icon: Boxes, name: 'Understand', body: 'Rules and a symbol resolver turn framework patterns into facts.' },
  { icon: Waypoints, name: 'Connect', body: 'Match calls to the services that receive them, across repositories.' },
  { icon: Cable, name: 'Serve', body: 'One MCP server answers any agent, and cites a source for every claim.' },
]

const USE_CASES: { icon: LucideIcon; name: string; question: string }[] = [
  { icon: GitPullRequest, name: 'Impact of a change', question: 'What does this change touch, and who is affected?' },
  { icon: Compass, name: 'Planning a piece of work', question: 'What will this work touch, and what already exists?' },
  { icon: Route, name: 'Working across a boundary', question: 'How do I call this service? Who depends on mine?' },
]

const PRINCIPLES: { icon: LucideIcon; text: string }[] = [
  { icon: Quote, text: 'Every answer cites its sources' },
  { icon: Eye, text: 'Gaps are shown, never hidden' },
  { icon: ScrollText, text: 'Deterministic rules produce the facts' },
  { icon: Lock, text: 'Read-only: it never changes your repositories' },
]

// What Steno is, at a glance: the first thing the UI shows
export function LandingPage({ onEnter, enterLabel }: { onEnter: () => void; enterLabel: string }) {
  return (
    <div className="landing">
      <header className="landing-bar">
        <div className="landing-brand">
          <Logo size={30} />
          <span>Steno</span>
        </div>
        <div className="landing-bar-tools">
          <ThemeToggle />
          <button className="btn btn-primary" onClick={onEnter}>
            {enterLabel}
            <ArrowRight size={15} />
          </button>
        </div>
      </header>

      <section className="landing-hero">
        <div className="landing-eyebrow">The context layer for AI agents</div>
        <h1>
          The whole organization,
          <br />
          <span>for any agent.</span>
        </h1>
        <p className="landing-lede">
          Steno gives any agent, wherever it runs, the context of the whole organization: how its software
          connects, what's changing and why, and what rules apply, with a source for every answer.
        </p>
      </section>

      <section className="landing-section">
        <h2>Three pillars, one interface</h2>
        <div className="landing-pillars">
          {PILLARS.map(({ icon: Icon, name, question, body, stage, live }) => (
            <article key={name} className={`landing-card pillar${live ? ' live' : ''}`}>
              <div className="pillar-top">
                <span className="landing-icon">
                  <Icon size={20} />
                </span>
                <span className={`landing-stage${live ? ' live' : ''}`}>{stage}</span>
              </div>
              <h3>{name}</h3>
              <div className="pillar-question">{question}</div>
              <p>{body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="landing-section">
        <h2>How it works</h2>
        <ol className="landing-steps">
          {STEPS.map(({ icon: Icon, name, body }, i) => (
            <li key={name} className="landing-card step">
              <div className="step-head">
                <span className="landing-icon small">
                  <Icon size={17} />
                </span>
                <span className="step-number">{i + 1}</span>
              </div>
              <h3>{name}</h3>
              <p>{body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="landing-section">
        <h2>Built for the questions agents get stuck on</h2>
        <div className="landing-uses">
          {USE_CASES.map(({ icon: Icon, name, question }) => (
            <div key={name} className="landing-use">
              <Icon size={18} />
              <div>
                <div className="use-name">{name}</div>
                <div className="use-question">“{question}”</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <footer className="landing-principles">
        {PRINCIPLES.map(({ icon: Icon, text }) => (
          <span key={text}>
            <Icon size={15} />
            {text}
          </span>
        ))}
      </footer>
    </div>
  )
}
