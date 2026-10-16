import { Alert, Button, Card, toast } from '@heroui/react'
import { useEffect, useState } from 'react'
import { api, money, privateFileUrl } from '../api'
import type { ClaimInfo } from '../api'
import { ErrorAlert, Field, Loading, PageTitle } from '../components/ui'
import { useMember } from '../hooks'

/** Receipts a check flagged, for the PICs: look at the receipt and the reasons, then accept or reject. */
export default function Review() {
  const { me } = useMember()
  const [claims, setClaims] = useState<ClaimInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.claimsForReview().then(setClaims).catch((e: Error) => setError(e.message))
  }, [])

  if (me?.role === 'member') return <PageTitle title="Receipts to check" subtitle="Only PICs check receipts." />
  if (error) return <ErrorAlert title="Couldn't load the receipts" message={error} />
  if (!claims) return <Loading />

  const decided = (id: number) => setClaims((list) => list?.filter((c) => c.id !== id) ?? null)
  return (
    <>
      <PageTitle title="Receipts to check"
                 subtitle="A check flagged these. Most are honest mistakes: look at the receipt, then decide." />
      {claims.length === 0 && <Card><Card.Content className="text-muted">Nothing to check right now.</Card.Content></Card>}
      <div className="flex flex-col gap-6">
        {claims.map((c) => <ReviewCard key={c.id} claim={c} canDecide={c.member !== me?.name} onDecided={decided} />)}
      </div>
    </>
  )
}

function ReviewCard({ claim, canDecide, onDecided }: {
  claim: ClaimInfo
  canDecide: boolean
  onDecided: (id: number) => void
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  const decide = async (accept: boolean) => {
    setBusy(true)
    try {
      if (accept) await api.acceptClaim(claim.id)
      else await api.rejectClaim(claim.id, note)
      toast.success(accept ? `Accepted: ${claim.member} is paid back after the hold.` : `Rejected: ${claim.member} has been told.`)
      onDecided(claim.id)
    } catch (e) {
      toast.danger((e as Error).message)
      setBusy(false)
    }
  }

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
            <Button isDisabled={busy} onPress={() => decide(true)}>Accept</Button>
            <Button variant="danger-soft" isDisabled={busy} onPress={() => decide(false)}>Reject</Button>
          </div>
        ) : <p className="text-sm text-muted">This is your own receipt: another PIC decides.</p>}
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
