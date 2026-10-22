import { Alert, Button, Card, toast } from '@heroui/react'
import { useEffect, useState } from 'react'
import { ago, api, dollars, money, privateFileUrl } from '../api'
import type { ClaimInfo, WaitingExtra, WaitingRequest } from '../api'
import { ErrorAlert, Field, Loading, PageTitle } from '../components/ui'
import { useMember } from '../hooks'

interface Waiting {
  requests: WaitingRequest[]
  extras: WaitingExtra[]
  claims: ClaimInfo[]
}

/** Everything waiting for a PIC: requests to approve, extras paid over a limit, and receipts a check flagged. */
export default function Review() {
  const { me } = useMember()
  const [waiting, setWaiting] = useState<Waiting | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.approvals(), api.claimsForReview()])
      .then(([approvals, claims]) => setWaiting({ ...approvals, claims }))
      .catch((e: Error) => setError(e.message))
  }, [])

  if (me?.role === 'member') return <PageTitle title="To review" subtitle="Only PICs approve requests and check receipts." />
  if (error) return <ErrorAlert title="Couldn't load what's waiting" message={error} />
  if (!waiting) return <Loading />

  const done = (key: keyof Waiting, id: number) => setWaiting((w) => w && {
    ...w,
    requests: key === 'requests' ? w.requests.filter((r) => r.id !== id) : w.requests,
    extras: key === 'extras' ? w.extras.filter((x) => x.claim_id !== id) : w.extras,
    claims: key === 'claims' ? w.claims.filter((c) => c.id !== id) : w.claims,
  })
  const mine = (name: string) => name === me?.name

  return (
    <>
      <PageTitle title="To review" subtitle="Waiting for a PIC's yes or no. Whoever asked is told either way." />
      <div className="flex flex-col gap-8">
        <Section title="Requests" empty="No requests waiting." count={waiting.requests.length}>
          {waiting.requests.map((r) => (
            <RequestCard key={r.id} request={r} canDecide={!mine(r.member)} onDecided={(id) => done('requests', id)} />
          ))}
        </Section>
        {waiting.extras.length > 0 && (
          <Section title="Paid over the limit" empty="" count={waiting.extras.length}>
            {waiting.extras.map((x) => (
              <ExtraCard key={x.claim_id} extra={x} canDecide={!mine(x.member)} onDecided={(id) => done('extras', id)} />
            ))}
          </Section>
        )}
        <Section title="Receipts to check" empty="No receipts to check." count={waiting.claims.length}>
          {waiting.claims.map((c) => (
            <ReceiptCard key={c.id} claim={c} canDecide={!mine(c.member)} onDecided={(id) => done('claims', id)} />
          ))}
        </Section>
      </div>
    </>
  )
}

function Section({ title, empty, count, children }: { title: string; empty: string; count: number; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold">{title}{count > 0 && <span className="text-muted"> ({count})</span>}</h2>
      {count === 0 ? <Card><Card.Content className="text-muted">{empty}</Card.Content></Card> : children}
    </section>
  )
}

/** Yes or no, with a note for a no. `decide` does the call; the card handles busy and the toast. */
function useDecision(onDone: () => void) {
  const [busy, setBusy] = useState(false)
  const decide = async (call: () => Promise<unknown>, said: string) => {
    setBusy(true)
    try {
      await call()
      toast.success(said)
      onDone()
    } catch (e) {
      toast.danger((e as Error).message)
      setBusy(false)
    }
  }
  return { busy, decide }
}

function Own() {
  return <p className="text-sm text-muted">This is your own: another PIC decides.</p>
}

function RequestCard({ request, canDecide, onDecided }: {
  request: WaitingRequest
  canDecide: boolean
  onDecided: (id: number) => void
}) {
  const [cap, setCap] = useState(request.cap_cents ? dollars(request.cap_cents) : '')
  const [note, setNote] = useState('')
  const { busy, decide } = useDecision(() => onDecided(request.id))
  const estimate = request.price ? `, ${request.price}` : ''

  return (
    <Card>
      <Card.Header>
        <Card.Title>{request.member}: {request.item}{request.quantity > 1 ? ` × ${request.quantity}` : ''}{estimate}</Card.Title>
        <Card.Description>Asked {ago(request.created_at)}</Card.Description>
      </Card.Header>
      <Card.Content className="flex flex-col gap-4">
        <Alert status="warning">
          <Alert.Indicator />
          <Alert.Content><Alert.Description>{request.reasons.join(' ')}</Alert.Description></Alert.Content>
        </Alert>
        <p className="text-sm">{request.reason ? <>Why: "{request.reason}"</> : <span className="text-muted">No reason given.</span>}</p>
        {canDecide ? (
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="sm:w-48"><Field label="Pay back up to ($)" value={cap} onChange={setCap} inputMode="decimal" /></div>
            <div className="grow"><Field label="Note to them (if you say no)" value={note} onChange={setNote} /></div>
            <Button isDisabled={busy} onPress={() => decide(() => api.approveRequest(request.id, cap),
              `Approved: ${request.member} can go ahead${cap ? `, up to $${cap}` : ''}.`)}>Approve</Button>
            <Button variant="danger-soft" isDisabled={busy} onPress={() => decide(() => api.rejectRequest(request.id, note),
              `Said no: ${request.member} has been told.`)}>Say no</Button>
          </div>
        ) : <Own />}
      </Card.Content>
    </Card>
  )
}

function ExtraCard({ extra, canDecide, onDecided }: {
  extra: WaitingExtra
  canDecide: boolean
  onDecided: (id: number) => void
}) {
  const [note, setNote] = useState('')
  const { busy, decide } = useDecision(() => onDecided(extra.claim_id))

  return (
    <Card>
      <Card.Header>
        <Card.Title>{extra.member}: {extra.item}, {money(extra.extra_cents)} over</Card.Title>
        <Card.Description>
          Paid {money(extra.total_cents)}; {money(extra.base_cents)} is paid back anyway, the rest only if you approve.
        </Card.Description>
      </Card.Header>
      <Card.Content className="flex flex-col gap-4">
        <p className="text-sm">Why: "{extra.extra_reason}"</p>
        {extra.receipt_url && <Preview url={extra.receipt_url} label="Receipt" />}
        {canDecide ? (
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="grow"><Field label="Note to them (if you say no)" value={note} onChange={setNote} /></div>
            <Button isDisabled={busy} onPress={() => decide(() => api.approveExtra(extra.claim_id),
              `Approved: ${extra.member} gets the extra ${money(extra.extra_cents)} after the rest.`)}>Approve</Button>
            <Button variant="danger-soft" isDisabled={busy} onPress={() => decide(() => api.rejectExtra(extra.claim_id, note),
              `Said no: ${extra.member} still gets ${money(extra.base_cents)}.`)}>Say no</Button>
          </div>
        ) : <Own />}
      </Card.Content>
    </Card>
  )
}

function ReceiptCard({ claim, canDecide, onDecided }: {
  claim: ClaimInfo
  canDecide: boolean
  onDecided: (id: number) => void
}) {
  const [note, setNote] = useState('')
  const { busy, decide } = useDecision(() => onDecided(claim.id))

  return (
    <Card>
      <Card.Header>
        <Card.Title>{claim.member}: {claim.item}, {money(claim.total_cents)}</Card.Title>
        <Card.Description>{claim.store}, {claim.receipt_date}</Card.Description>
      </Card.Header>
      <Card.Content className="flex flex-col gap-4">
        <Alert status="warning">
          <Alert.Indicator />
          <Alert.Content><Alert.Description>{claim.reasons.join(' ')}</Alert.Description></Alert.Content>
        </Alert>
        {claim.extra_cents > 0 && (
          <p className="text-sm">{money(claim.extra_cents)} over the limit: "{claim.extra_reason}"</p>
        )}
        <div className="flex flex-wrap gap-4">
          <Preview url={claim.receipt_url} label="Receipt" />
          {claim.photo_url && <Preview url={claim.photo_url} label="Where it's kept" />}
        </div>
        {canDecide ? (
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="grow"><Field label="Note to them (if you reject it)" value={note} onChange={setNote} /></div>
            <Button isDisabled={busy} onPress={() => decide(() => api.acceptClaim(claim.id),
              `Accepted: ${claim.member} is paid back after the hold.`)}>Accept</Button>
            <Button variant="danger-soft" isDisabled={busy} onPress={() => decide(() => api.rejectClaim(claim.id, note),
              `Rejected: ${claim.member} has been told.`)}>Reject</Button>
          </div>
        ) : <Own />}
      </Card.Content>
    </Card>
  )
}

/** A receipt or photo: images inline, PDFs as a link. Fetched with the member header (the API checks who's asking). */
function Preview({ url, label }: { url: string; label: string }) {
  const [src, setSrc] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let local: string | null = null
    privateFileUrl(url).then((u) => { local = u; setSrc(u) }).catch(() => setFailed(true))
    return () => { if (local) URL.revokeObjectURL(local) }
  }, [url])

  if (failed) return <p className="text-sm text-muted">{label}: couldn't load it.</p>
  if (!src) return <p className="text-sm text-muted">{label}: loading…</p>
  return url.endsWith('.pdf')
    ? <a href={src} target="_blank" rel="noreferrer" className="link text-sm">{label} (PDF)</a>
    : <a href={src} target="_blank" rel="noreferrer"><img src={src} alt={label} className="max-h-72 rounded-lg border border-border" /></a>
}
