import { Alert, Button, Card } from '@heroui/react'
import { useEffect, useRef, useState } from 'react'
import { ApiError, api, money, toCents, when } from '../api'
import type { Claimable, ReadResult, Submitted } from '../api'
import { AreaField, Choice, ErrorAlert, Field, Loading, PageTitle } from '../components/ui'

/** Hand in the receipt for something you got for the group. Code checks it; anything unusual goes to a PIC. */
export default function Claim() {
  const [claimable, setClaimable] = useState<Claimable[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<Submitted | null>(null)

  const load = () => api.claimable().then(setClaimable).catch((e: Error) => setError(e.message))
  useEffect(() => { load() }, [])

  if (error) return <ErrorAlert title="Couldn't load your requests" message={error} />
  if (!claimable) return <Loading />

  return (
    <>
      <PageTitle title="Hand in a receipt" subtitle="You're paid back through PayPal after the group's hold (3 days by default)." />
      {done && <Result submitted={done} onAnother={() => { setDone(null); load() }} />}
      {!done && (claimable.length === 0
        ? <Card><Card.Content className="text-muted">Nothing to claim yet: tell the group what you're getting first, then hand in the receipt here.</Card.Content></Card>
        : <ClaimForm key={claimable.map((c) => c.request_id).join()} claimable={claimable} onDone={setDone} />)}
    </>
  )
}

function ClaimForm({ claimable, onDone }: { claimable: Claimable[]; onDone: (s: Submitted) => void }) {
  const [requestId, setRequestId] = useState(String(claimable[0].request_id))
  const [receipt, setReceipt] = useState<File | null>(null)
  const [photo, setPhoto] = useState<File | null>(null)
  const [store, setStore] = useState('')
  const [day, setDay] = useState(() => new Date().toISOString().slice(0, 10))  // today (UTC, like the app)
  const [total, setTotal] = useState('')
  const [reason, setReason] = useState('')
  const [problem, setProblem] = useState<{ message: string; missing: string[] } | null>(null)
  const [sending, setSending] = useState(false)

  const chosen = claimable.find((c) => String(c.request_id) === requestId) ?? claimable[0]
  const totalCents = toCents(total)
  const over = totalCents !== null && totalCents > chosen.cap_cents
  const [read, setRead] = useState<ReadState>({ status: 'idle' })
  const lastRead = useRef(0)

  /** Read the receipt (again, when it's for another request), and fill in the form from what it says. */
  const startReading = (file: File | null, forRequest: number) => {
    const n = ++lastRead.current
    if (!file) { setRead({ status: 'idle' }); return }
    setRead({ status: 'reading' })
    const form = new FormData()
    form.append('request_id', String(forRequest))
    form.append('receipt', file)
    api.readReceipt(form)
      .then((result) => {
        if (n !== lastRead.current) return  // a newer read started
        const reading = result.reading
        if (reading?.readable) {
          if (reading.store) setStore(reading.store)
          if (reading.date) setDay(reading.date)
          // a total in another currency isn't dollars: the member types what they paid in the group's currency
          if (reading.total && (!reading.currency || reading.currency === result.currency)) setTotal(reading.total)
        }
        setRead({ status: 'done', result })
      })
      .catch((e: Error) => n === lastRead.current && setRead({ status: 'done', result: { reading: null, note: e.message, currency: '' } }))
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const form = new FormData()
    form.append('request_id', String(chosen.request_id))
    form.append('store', store)
    form.append('receipt_date', day)
    form.append('total', total)
    if (over) form.append('extra_reason', reason)
    if (receipt) form.append('receipt', receipt)
    if (photo && chosen.needs_photo) form.append('photo', photo)
    setSending(true)
    try {
      onDone(await api.submitClaim(form))
    } catch (err) {
      const missing = err instanceof ApiError ? ((err.data as { missing?: string[] } | null)?.missing ?? []) : []
      setProblem({ message: (err as Error).message, missing })
    } finally {
      setSending(false)
    }
  }

  return (
    <Card>
      <Card.Content>
        <form onSubmit={submit} className="flex flex-col gap-5">
          {claimable.length > 1
            ? <Choice label="What's it for?" value={String(chosen.request_id)}
                      onChange={(id) => { setRequestId(id); if (receipt) startReading(receipt, Number(id)) }}
                      options={claimable.map((c) => ({ id: String(c.request_id), label: label(c) }))} />
            : <p><span className="text-muted">For: </span><b>{label(chosen)}</b></p>}
          {chosen.status === 'expired' && (
            <Alert status="warning">
              <Alert.Indicator />
              <Alert.Content>
                <Alert.Description>Your time to get it ran out, so a PIC will look at this receipt first.</Alert.Description>
              </Alert.Content>
            </Alert>
          )}

          <FilePick label="Receipt" hint="A photo or a PDF of the receipt: the store, date and total are filled in from it."
                    file={receipt} onChange={(file) => { setReceipt(file); startReading(file, chosen.request_id) }} />
          <ReadSummary read={read} item={chosen.item} />
          {chosen.needs_photo && (
            <FilePick label="Where you put it" hint={`A photo of the ${chosen.item.toLowerCase()} where they're kept, so others can find them.`}
                      file={photo} onChange={setPhoto} />
          )}

          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Store" value={store} onChange={setStore} placeholder="Riverside Hardware" isRequired />
            <Field label="Date on the receipt" type="date" value={day} onChange={setDay} isRequired />
            <Field label="Total ($)" value={total} onChange={setTotal} inputMode="decimal" placeholder="18.40" isRequired
                   description={`Paid back up to ${money(chosen.cap_cents)}.`} />
          </div>
          {over && (
            <AreaField label="Why did it cost more?" value={reason} onChange={setReason} rows={2} isRequired
                       description={`${money(chosen.cap_cents)} is paid back after the hold; a PIC decides on the other ${money(totalCents! - chosen.cap_cents)}.`} />
          )}

          {problem && (
            <ErrorAlert title={problem.missing.length ? 'Still needed' : 'Not handed in'}
                        message={problem.missing.length ? problem.missing.join(', ') : problem.message} />
          )}
          <div><Button type="submit" isDisabled={sending}>{sending ? 'Handing in…' : 'Hand in the receipt'}</Button></div>
        </form>
      </Card.Content>
    </Card>
  )
}

function Result({ submitted, onAnother }: { submitted: Submitted; onAnother: () => void }) {
  const { outcome, claim } = submitted
  return (
    <Card>
      <Card.Content className="flex flex-col gap-3">
        {outcome === 'accepted' ? (
          <Alert status="success">
            <Alert.Indicator />
            <Alert.Content>
              <Alert.Title>Thanks! The group can see you got {claim.item.toLowerCase()}.</Alert.Title>
              <Alert.Description>
                {money(claim.base_cents)} is paid back through PayPal
                {claim.payout_due_at ? ` on ${when(claim.payout_due_at)}` : ' after the hold'}, unless someone reports a problem first.
                {claim.extra_cents > 0 && ` A PIC decides on the other ${money(claim.extra_cents)}.`}
              </Alert.Description>
            </Alert.Content>
          </Alert>
        ) : (
          <Alert status="warning">
            <Alert.Indicator />
            <Alert.Content>
              <Alert.Title>Handed in: a PIC will check it first</Alert.Title>
              <Alert.Description>{claim.reasons.join(' ')}</Alert.Description>
            </Alert.Content>
          </Alert>
        )}
        <div><Button variant="secondary" onPress={onAnother}>Hand in another</Button></div>
      </Card.Content>
    </Card>
  )
}

type ReadState = { status: 'idle' } | { status: 'reading' } | { status: 'done'; result: ReadResult }

/** What the reader saw, and what a PIC will be asked to look at because of it. */
function ReadSummary({ read, item }: { read: ReadState; item: string }) {
  if (read.status === 'idle') return null
  if (read.status === 'reading') return <p className="text-sm text-muted">Reading the receipt…</p>
  const { reading, note, currency } = read.result
  if (!reading) return <p className="text-sm text-muted">{note}</p>
  if (!reading.readable) {
    return <Note status="warning" title="Couldn't read this receipt">Fill in the store, date and total: a PIC will check them against the receipt.</Note>
  }
  const otherCurrency = reading.currency && reading.currency !== currency
  return (
    <div className="flex flex-col gap-2">
      <Note status="success" title="Read from the receipt">
        {[reading.store, reading.date_as_printed ?? reading.date, reading.total && `${reading.currency ?? ''} ${reading.total}`.trim()]
          .filter(Boolean).join(' · ')}. Check the values below; change them if they're wrong.
      </Note>
      {otherCurrency && <Note status="warning">It's in {reading.currency}: the group pays back in {currency}, so enter what you paid in {currency}. A PIC will look at it.</Note>}
      {reading.has_the_item === 'no' && <Note status="warning">Nothing on it looks like {item.toLowerCase()}, so a PIC will look at it.</Note>}
      {(reading.lines.length > 0 || reading.unsure.length > 0) && (
        <details className="text-sm text-muted">
          <summary className="cursor-pointer">What it read ({reading.lines.length} lines)</summary>
          <ul className="mt-1 list-disc pl-5">
            {reading.lines.map((line, i) => <li key={i}>{line.text}</li>)}
            {reading.unsure.map((u, i) => <li key={`u${i}`} className="italic">Unsure: {u}</li>)}
          </ul>
        </details>
      )}
    </div>
  )
}

function Note({ status, title, children }: { status: 'success' | 'warning'; title?: string; children: React.ReactNode }) {
  return (
    <Alert status={status}>
      <Alert.Indicator />
      <Alert.Content>
        {title && <Alert.Title>{title}</Alert.Title>}
        <Alert.Description>{children}</Alert.Description>
      </Alert.Content>
    </Alert>
  )
}

function FilePick({ label, hint, file, onChange }: {
  label: string
  hint: string
  file: File | null
  onChange: (file: File | null) => void
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-sm font-medium">{label} <span className="text-danger">*</span></span>
      <input type="file" accept="image/*,application/pdf" onChange={(e) => onChange(e.target.files?.[0] ?? null)}
             className="rounded-xl border border-border bg-surface px-3 py-2 text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-default file:px-3 file:py-1" />
      <span className="text-xs text-muted">{file ? `${file.name} (${Math.ceil(file.size / 1024)} KB)` : hint}</span>
    </label>
  )
}

function label(c: Claimable) {
  return `${c.item} (up to ${money(c.cap_cents)})` + (c.until ? `, ${c.status === 'expired' ? 'was due' : 'due'} ${when(c.until)}` : '')
}
