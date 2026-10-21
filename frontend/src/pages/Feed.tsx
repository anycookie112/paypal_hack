import { Alert, Button, Card, Chip, toast } from '@heroui/react'
import { useEffect, useState } from 'react'
import { ago, api, when } from '../api'
import type { FeedItem } from '../api'
import { AreaField, ErrorAlert, FormDialog, Loading, PageTitle } from '../components/ui'

/** What the group sees: who's getting what, who got what. 👍 to say thanks; "something's wrong?" during the hold. */
export default function Feed() {
  const [posts, setPosts] = useState<FeedItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [flagging, setFlagging] = useState<FeedItem | null>(null)

  const load = () => api.feed().then(setPosts).catch((e: Error) => setError(e.message))
  useEffect(() => { load() }, [])

  const like = async (post: FeedItem) => {
    try {
      const { likes } = await api.like(post.post_id)
      setPosts((list) => list?.map((p) => p.post_id === post.post_id ? { ...p, likes, liked: !p.liked } : p) ?? null)
    } catch (e) {
      toast.danger((e as Error).message)
    }
  }

  if (error) return <ErrorAlert title="Couldn't load the feed" message={error} />
  if (!posts) return <Loading />
  return (
    <>
      <PageTitle title="Feed" subtitle="Who's getting what, and who got it. Problems never go here: they go privately to the treasurer." />
      {posts.length === 0 && <Card><Card.Content className="text-muted">Nothing yet.</Card.Content></Card>}
      <div className="flex flex-col gap-3">
        {posts.map((p) => (
          <Card key={p.post_id}>
            <Card.Content>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
                <span aria-hidden className="text-xl">{p.kind === 'on_it' ? '🛒' : '✅'}</span>
                <div className="min-w-0 grow text-left">
                  <p className="font-medium">{p.text}</p>
                  <p className="text-xs text-muted">{ago(p.created_at)}</p>
                </div>
                <Status post={p} />
                <Button size="sm" variant={p.liked ? 'secondary' : 'ghost'} onPress={() => like(p)}
                        aria-label={p.liked ? 'Take back your thumbs up' : 'Thumbs up'}>
                  👍 {p.likes > 0 && p.likes}
                </Button>
                {p.can_flag && <Button size="sm" variant="ghost" onPress={() => setFlagging(p)}>Something's wrong?</Button>}
              </div>
            </Card.Content>
          </Card>
        ))}
      </div>
      {flagging && <SomethingsWrong post={flagging} onClose={() => setFlagging(null)} onDone={load} />}
    </>
  )
}

function Status({ post }: { post: FeedItem }) {
  if (post.kind === 'on_it') {
    if (post.status === 'on_it') return <Chip size="sm" variant="soft" color="accent">until {post.until ? when(post.until) : '—'}</Chip>
    if (post.status === 'claimed') return <Chip size="sm" variant="soft" color="success">got it</Chip>
    return <Chip size="sm" variant="soft">{post.status === 'expired' ? 'time ran out' : post.status}</Chip>
  }
  const label: Record<string, string> = { waiting: 'in its hold', held: 'on hold', review: 'being checked', paid: 'paid back' }
  return <Chip size="sm" variant="soft" color={post.status === 'paid' ? 'success' : 'default'}>{label[post.status] ?? post.status}</Chip>
}

/** Two steps (docs/SPEC.md §4.1): first ask the person who bought it; only if that doesn't sort it, a formal report. */
function SomethingsWrong({ post, onClose, onDone }: { post: FeedItem; onClose: () => void; onDone: () => void }) {
  const [step, setStep] = useState<'ask' | 'report'>('ask')
  const [text, setText] = useState('')
  const [checked, setChecked] = useState('')
  const buyer = post.member

  const save = async () => {
    try {
      if (step === 'ask') {
        await api.askBuyer(post.claim_id!, text)
        toast.success(`Asked ${buyer}: their answer will come to you.`)
      } else {
        await api.report(post.claim_id!, text, checked)
        toast.success('Report filed: the treasurer will look into it privately.')
      }
      onDone()
      return null
    } catch (e) {
      return (e as Error).message
    }
  }

  return (
    <FormDialog title={step === 'ask' ? `Ask ${buyer}` : 'File a formal report'} onClose={onClose} save={save}
                saveLabel={step === 'ask' ? `Ask ${buyer}` : 'File the report'}>
      {step === 'ask' ? (
        <>
          <p className="text-sm text-muted">Usually it's just somewhere else. {buyer} is asked where it is; nothing else happens.</p>
          <AreaField label="What do you want to know?" value={text} onChange={setText} rows={2} isRequired
                     placeholder="I can't find them in the supply box" />
          <button type="button" className="link self-start text-sm" onClick={() => setStep('report')}>
            Already asked, and it's still not sorted?
          </button>
        </>
      ) : (
        <>
          <Alert status="warning">
            <Alert.Indicator />
            <Alert.Content>
              <Alert.Description>
                This goes privately to the treasurer and holds {buyer}'s payout longer. {buyer} is told neutrally.
              </Alert.Description>
            </Alert.Content>
          </Alert>
          <AreaField label="What's wrong?" value={text} onChange={setText} rows={2} isRequired />
          <AreaField label="What did you check first?" value={checked} onChange={setChecked} rows={2} isRequired
                     placeholder={`Where it's kept; asked ${buyer}`} />
        </>
      )}
    </FormDialog>
  )
}
