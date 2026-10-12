import { Card } from '@heroui/react'
import { PageTitle } from '../components/ui'
import { useMeta } from '../hooks'

/** Placeholder until the employee and admin pages are built (docs/SPEC.md §15, week 2). */
export default function Home() {
  const meta = useMeta()
  return (
    <>
      <PageTitle title={meta?.app_name ?? 'Office Fund'}
                 subtitle="Ask for office essentials, buy them, and get paid back through PayPal." />
      <Card>
        <Card.Content className="text-muted">The app is being rebuilt for the office fund. Pages come next.</Card.Content>
      </Card>
    </>
  )
}
