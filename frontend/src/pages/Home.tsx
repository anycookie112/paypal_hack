import { Alert, Button, Card, Chip, Label, Meter, toast } from '@heroui/react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { ago, api, money, when } from '../api'
import type { Budget, Home as HomeData, Question } from '../api'
import { ErrorAlert, Field, Loading, PageTitle } from '../components/ui'

/** The member's own page: what they can still spend, what they're getting, what's being paid back, and news. */
export default function Home() {
  const [home, setHome] = useState<HomeData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => api.home().then(setHome).catch((e: Error) => setError(e.message))
  useEffect(() => { load() }, [])

  if (error) return <ErrorAlert title="Couldn't load your page" message={error} />
  if (!home) return <Loading />

  const { me } = home
  const release = async (requestId: number, item: string) => {
    try {
      await api.release(requestId)
      toast.success(`${item} is free for someone else.`)
      load()
    } catch (e) {
      toast.danger((e as Error).message)
    }
  }

  return (
    <>
      <PageTitle title={`Hi ${me.name}`} subtitle={<>Tell the group before you buy: <Link to="/chat" className="link">chat</Link>,
        and hand in the receipt on <Link to="/claim" className="link">Claim</Link>.</>} />
      <div className="flex flex-col gap-6">
        {!home.paypal_email_set && (
          <Alert status="warning">
            <Alert.Indicator />
            <Alert.Content>
              <Alert.Title>Add your PayPal email</Alert.Title>
              <Alert.Description>It's where you're paid back: tell the AI in <Link to="/chat" className="link">chat</Link>.</Alert.Description>
            </Alert.Content>
          </Alert>
        )}

        {home.questions_for_me.map((q) => <AnswerCard key={q.question_id} question={q} onAnswered={load} />)}

        <div className="grid gap-6 md:grid-cols-2">
          <Card>
            <Card.Header><Card.Title>Your month</Card.Title></Card.Header>
            <Card.Content>
              <BudgetMeter budget={{ name: 'Spent or promised', budget_cents: me.monthly_limit_cents, committed_cents: me.committed_cents }} />
            </Card.Content>
          </Card>

          <Card>
            <Card.Header><Card.Title>You're getting</Card.Title></Card.Header>
            <Card.Content className="flex flex-col gap-3">
              {me.open_requests.length === 0 && <p className="text-muted">Nothing right now.</p>}
              {me.open_requests.map((r) => (
                <div key={r.request_id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="font-medium">{r.item}</span>
                  {r.status === 'on_it'
                    ? <span className="text-sm text-muted">up to {money(r.cap_cents)}, by {r.until ? when(r.until) : '—'}</span>
                    : <Chip size="sm" variant="soft" color="warning">waiting for a PIC</Chip>}
                  <div className="ml-auto flex gap-2">
                    {r.status === 'on_it' && <Link to="/claim"><Button size="sm" variant="secondary">Hand in receipt</Button></Link>}
                    <Button size="sm" variant="ghost" onPress={() => release(r.request_id, r.item)}>Can't get it</Button>
                  </div>
                </div>
              ))}
            </Card.Content>
          </Card>
        </div>

        {me.claims_waiting.length > 0 && (
          <Card>
            <Card.Header><Card.Title>Being paid back</Card.Title></Card.Header>
            <Card.Content className="flex flex-col gap-2">
              {me.claims_waiting.map((c) => (
                <div key={c.claim_id} className="flex flex-wrap items-center gap-x-3">
                  <span className="font-medium">{c.item}</span>
                  <span>{money(c.total_cents)}</span>
                  <span className="text-sm text-muted">
                    {c.status === 'review' ? 'a PIC is checking the receipt first'
                      : c.status === 'held' ? `on hold: someone reported a problem${c.payout_due_at ? ` (until ${when(c.payout_due_at)})` : ''}`
                      : c.payout_due_at ? `paid through PayPal on ${when(c.payout_due_at)}` : 'waiting'}
                  </span>
                </div>
              ))}
            </Card.Content>
          </Card>
        )}

        <div className="grid gap-6 md:grid-cols-2">
          <Card>
            <Card.Header className="flex flex-row items-center">
              <Card.Title>News {home.unread > 0 && <Chip size="sm" color="accent" className="ml-2">{home.unread} new</Chip>}</Card.Title>
              {home.unread > 0 && (
                <Button size="sm" variant="ghost" className="ml-auto" onPress={() => api.markRead().then(load)}>Mark all read</Button>
              )}
            </Card.Header>
            <Card.Content className="flex flex-col gap-3">
              {home.notifications.length === 0 && <p className="text-muted">Nothing yet.</p>}
              {home.notifications.map((n) => (
                <div key={n.id} className={n.read ? 'text-muted' : ''}>
                  <p className="text-sm">{n.text}</p>
                  <p className="text-xs text-muted">{ago(n.created_at)}</p>
                </div>
              ))}
            </Card.Content>
          </Card>

          <Card>
            <Card.Header><Card.Title>The group this month</Card.Title></Card.Header>
            <Card.Content className="flex flex-col gap-4">
              <BudgetMeter budget={{ ...home.group.overall, name: 'Everything' }} />
              {home.group.categories.map((b) => <BudgetMeter key={b.name} budget={b} />)}
            </Card.Content>
          </Card>
        </div>
      </div>
    </>
  )
}

function BudgetMeter({ budget }: { budget: Budget }) {
  if (!budget.budget_cents) {
    return <p className="text-sm"><span className="font-medium">{budget.name}</span>: {money(budget.committed_cents)} (no budget set)</p>
  }
  const left = budget.budget_cents - budget.committed_cents
  const share = budget.committed_cents / budget.budget_cents
  return (
    <Meter value={Math.min(budget.committed_cents, budget.budget_cents)} maxValue={budget.budget_cents} size="sm"
           color={share >= 1 ? 'danger' : share >= 0.8 ? 'warning' : 'accent'}
           formatOptions={{ style: 'currency', currency: 'USD' }} aria-label={budget.name}>
      <div className="flex w-full justify-between text-sm">
        <Label>{budget.name}</Label>
        <span className="text-muted">{left >= 0 ? `${money(left)} left of ${money(budget.budget_cents)}` : `${money(-left)} over`}</span>
      </div>
      <Meter.Track><Meter.Fill /></Meter.Track>
    </Meter>
  )
}

function AnswerCard({ question, onAnswered }: { question: Question; onAnswered: () => void }) {
  const [answer, setAnswer] = useState('')
  const [sending, setSending] = useState(false)
  const send = async (e: React.FormEvent) => {
    e.preventDefault()
    setSending(true)
    try {
      await api.answer(question.question_id, answer)
      toast.success(`${question.asked_by} has been told.`)
      onAnswered()
    } catch (err) {
      toast.danger((err as Error).message)
      setSending(false)
    }
  }
  return (
    <Card>
      <Card.Header>
        <Card.Title>{question.asked_by} is asking about the {question.item.toLowerCase()} you got</Card.Title>
        <Card.Description>"{question.text}"</Card.Description>
      </Card.Header>
      <Card.Content>
        <form onSubmit={send} className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="grow"><Field label="Where is it?" value={answer} onChange={setAnswer} placeholder="In the van" isRequired /></div>
          <Button type="submit" isDisabled={sending}>Answer</Button>
        </form>
      </Card.Content>
    </Card>
  )
}
