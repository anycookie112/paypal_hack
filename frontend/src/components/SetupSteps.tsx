import { Link } from 'react-router'

const STEPS = [
  { key: 'profile', label: 'Business' },
  { key: 'products', label: 'Products' },
  { key: 'rules', label: 'Rules & payments' },
  { key: 'done', label: 'Done' },
] as const

export type StepKey = (typeof STEPS)[number]['key']

/** Numbered onboarding progress. Steps after "Business" are links once the business exists. */
export function SetupSteps({ current, slug }: { current: StepKey; slug?: string }) {
  const currentIndex = STEPS.findIndex((s) => s.key === current)
  return (
    <ol className="mb-6 flex flex-wrap items-center gap-2 text-sm">
      {STEPS.map((step, i) => {
        const state = i === currentIndex ? 'current' : i < currentIndex ? 'done' : 'todo'
        const badge = (
          <span
            className={[
              'inline-flex size-6 items-center justify-center rounded-full text-xs font-semibold',
              state === 'current' ? 'bg-accent text-accent-foreground'
                : state === 'done' ? 'bg-success-soft text-success' : 'border border-border text-muted',
            ].join(' ')}
          >
            {state === 'done' ? '✓' : i + 1}
          </span>
        )
        const label = <span className={state === 'current' ? 'font-semibold' : 'text-muted'}>{step.label}</span>
        const linkable = slug && step.key !== 'profile' && i !== currentIndex
        return (
          <li key={step.key} className="flex items-center gap-2">
            {linkable ? (
              <Link to={`/b/${slug}/setup/${step.key}`} className="flex items-center gap-2 hover:opacity-80">
                {badge}{label}
              </Link>
            ) : (
              <span className="flex items-center gap-2">{badge}{label}</span>
            )}
            {i < STEPS.length - 1 && <span className="mx-1 text-muted">›</span>}
          </li>
        )
      })}
    </ol>
  )
}
