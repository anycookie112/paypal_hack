import { Button, Card, Chip, Spinner, Switch, toast } from '@heroui/react'
import { useEffect, useRef, useState } from 'react'
import { api, money, ROLE_LABEL, say } from '../api'
import type { ChatMessage, LabEntry, LabStatus, MemberInfo } from '../api'
import { Choice, FormDialog, PageTitle } from '../components/ui'
import { useMember } from '../hooks'
import { Bubble } from './Chat'

// The demo story's lines (docs/SPEC.md §11), one tap each.
const LINES: Record<string, string[]> = {
  Priya: ["We're out of gloves for Saturday's cleanup, I'll grab some", 'by 11'],
  Marco: ["I'll pick up gloves on my way in", 'Can I get a folding table for the market stall? About $40',
          'We need it to show the flyers at the Saturday market', 'Did Priya say where the gloves are?'],
  Dana: ['How much is left in the supplies budget this month?'],
  Sam: ['Who are the PICs?'],
}
const stamp = () => new Date().toISOString()

/** Priya, Marco, Dana, Sam: the order they come in in the story; anyone else after them. */
const inStoryOrder = (members: MemberInfo[]) => {
  const rank = (m: MemberInfo) => { const i = Object.keys(LINES).indexOf(m.name); return i < 0 ? 99 : i }
  return [...members].sort((a, b) => rank(a) - rank(b))
}

/** The testing grounds: every member's chat side by side, and what the AI did and where things stand. */
export default function Lab() {
  const { members } = useMember()
  const [round, setRound] = useState(0)  // bumped on reset: every panel loads again
  const [resetting, setResetting] = useState(false)

  return (
    <>
      <div className="flex flex-wrap items-start gap-4">
        <div className="grow">
          <PageTitle title="Lab" subtitle="Talk as everyone at once, and watch what the AI does and what changes." />
        </div>
        <Button variant="danger-soft" onPress={() => setResetting(true)}>Reset the demo</Button>
      </div>
      <div key={round} className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="grid content-start gap-4 md:grid-cols-2">
          {inStoryOrder(members).map((m) => <ChatBox key={m.id} member={m} />)}
        </div>
        <div className="flex flex-col gap-4">
          <StatusPanel />
          <LogPanel members={members} />
        </div>
      </div>
      {resetting && (
        <FormDialog title="Reset the demo?" saveLabel="Reset" onClose={() => setResetting(false)} save={async () => {
          try {
            await api.labReset()
            toast.success('The demo starts over.')
            setRound((r) => r + 1)
            return null
          } catch (e) {
            return (e as Error).message
          }
        }}>
          <p>Every chat, request, receipt and log entry is deleted, and the demo group is set up again.</p>
        </FormDialog>
      )}
    </>
  )
}

/** One member's chat: their conversation, the reply as it's written, and their demo lines. */
function ChatBox({ member }: { member: MemberInfo }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [reply, setReply] = useState<{ text: string; step: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.chat(member.id).then((c) => setMessages(c.messages)).catch((e: Error) => setError(e.message))
  }, [member.id])
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight }) }, [messages, reply])

  const send = async (text: string) => {
    text = text.trim()
    if (!text || reply) return
    setMessages((m) => [...m, { role: 'user', text, created_at: stamp() }])
    setDraft('')
    setError(null)
    setReply({ text: '', step: '' })
    try {
      await say(text, (event) => {
        if (event.type === 'step') setReply((r) => r && { ...r, step: event.text })
        else if (event.type === 'text') setReply((r) => r && { ...r, text: r.text + event.text })
        else setMessages((m) => [...m, { role: 'assistant', text: event.text, created_at: stamp() }])
      }, member.id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setReply(null)
    }
  }

  return (
    <Card className="flex h-[26rem] flex-col">
      <Card.Header className="flex flex-row items-center gap-2">
        <Card.Title>{member.name}</Card.Title>
        <Chip size="sm" variant="soft">{ROLE_LABEL[member.role]}</Chip>
      </Card.Header>
      <Card.Content className="flex min-h-0 grow flex-col gap-2">
        <div ref={box} className="flex min-h-0 grow flex-col gap-2 overflow-y-auto pr-1">
          {messages.map((m, i) => <Bubble key={i} role={m.role} text={m.text} />)}
          {reply && (reply.text
            ? <Bubble role="assistant" text={reply.text} />
            : <div className="flex items-center gap-2 text-xs text-muted"><Spinner size="sm" /> {reply.step || 'Thinking…'}</div>)}
        </div>
        {error && <p className="text-xs text-danger">{error}</p>}
        <div className="flex flex-wrap gap-1">
          {(LINES[member.name] ?? []).map((line) => (
            <button key={line} type="button" disabled={!!reply} onClick={() => send(line)}
                    className="max-w-full truncate rounded-full border border-border px-2 py-0.5 text-xs text-muted hover:text-foreground disabled:opacity-50">
              {line}
            </button>
          ))}
        </div>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); send(draft) }}>
          <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={`Message as ${member.name}`}
                 aria-label={`Message as ${member.name}`}
                 className="min-w-0 grow rounded-lg border border-border bg-transparent px-3 py-1.5 text-sm" />
          <Button size="sm" type="submit" isDisabled={!draft.trim() || !!reply}>Send</Button>
        </form>
      </Card.Content>
    </Card>
  )
}

/** Open requests, receipts on their way to a payout, and the latest moves. Refreshed every few seconds. */
function StatusPanel() {
  const [status, setStatus] = useState<LabStatus | null>(null)

  useEffect(() => {
    const load = () => api.labStatus().then(setStatus).catch(() => {})
    load()
    const timer = setInterval(load, 3000)
    return () => clearInterval(timer)
  }, [])

  const time = (iso: string | null) => iso
    ? new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) : '—'
  return (
    <Card>
      <Card.Header><Card.Title>Where things stand</Card.Title></Card.Header>
      <Card.Content className="grid gap-4 text-sm md:grid-cols-2">
        <div className="flex flex-col gap-1">
          <p className="font-medium">Requests</p>
          {status?.requests.length === 0 && <p className="text-muted">None open.</p>}
          {status?.requests.map((r) => (
            <p key={r.id}>
              <span className="text-muted">#{r.id}</span> {r.member}: {r.item}{' '}
              {r.status === 'on_it'
                ? <span className="text-muted">getting it, up to {money(r.cap_cents)}, until {time(r.until)}</span>
                : <Chip size="sm" variant="soft" color="warning">waiting for a PIC</Chip>}
            </p>
          ))}
          <p className="mt-2 font-medium">Receipts</p>
          {status?.claims.length === 0 && <p className="text-muted">None on their way.</p>}
          {status?.claims.map((c) => (
            <p key={c.id}>
              <span className="text-muted">#{c.id}</span> {c.member}: {c.item}, {money(c.total_cents)}{' '}
              <span className="text-muted">{c.status}{c.payout_due_at ? `, pays ${time(c.payout_due_at)}` : ''}</span>
            </p>
          ))}
        </div>
        <div className="flex flex-col gap-1">
          <p className="font-medium">Latest moves</p>
          {status?.moves.length === 0 && <p className="text-muted">Nothing has moved yet.</p>}
          <div className="flex max-h-48 flex-col gap-1 overflow-y-auto">
            {status?.moves.map((m, i) => <p key={i} className="text-xs">{m.text}</p>)}
          </div>
        </div>
      </Card.Content>
    </Card>
  )
}

/** Every step the AI took, for everyone, live: model calls (time, tokens), tool calls and results, rejected
 *  results, replies. Subagents' steps are indented under the coordinator's. Click a step for its full data. */
function LogPanel({ members }: { members: MemberInfo[] }) {
  const [entries, setEntries] = useState<LabEntry[]>([])
  const [who, setWho] = useState('all')
  const [showModel, setShowModel] = useState(false)
  const last = useRef(0)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const poll = () => api.labLog(last.current).then((more) => {
      if (!more.length) return
      last.current = more[more.length - 1].id
      setEntries((e) => [...e, ...more].slice(-1000))
    }).catch(() => {})
    poll()
    const timer = setInterval(poll, 1500)
    return () => clearInterval(timer)
  }, [])

  const shown = entries.filter((e) => (who === 'all' || e.member === who) && (showModel || e.event !== 'model'))
  useEffect(() => {
    const el = box.current
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) el.scrollTo({ top: el.scrollHeight })
  }, [shown.length])

  return (
    <Card className="flex h-[40rem] flex-col">
      <Card.Header className="flex flex-row flex-wrap items-center gap-3">
        <Card.Title>AI log</Card.Title>
        <div className="ml-auto w-36">
          <Choice ariaLabel="Whose steps" value={who} onChange={setWho}
                  options={[{ id: 'all', label: 'Everyone' }, ...members.map((m) => ({ id: m.name, label: m.name }))]} />
        </div>
        <Switch size="sm" isSelected={showModel} onChange={setShowModel}>
          <Switch.Control><Switch.Thumb /></Switch.Control>
          <span className="whitespace-nowrap text-sm">Model calls</span>
        </Switch>
      </Card.Header>
      <Card.Content className="min-h-0 grow">
        <div ref={box} className="flex h-full flex-col gap-1 overflow-y-auto font-mono text-xs">
          {shown.length === 0 && <p className="font-sans text-sm text-muted">Nothing yet: say something in a chat.</p>}
          {shown.map((e) => <LogLine key={e.id} entry={e} />)}
        </div>
      </Card.Content>
    </Card>
  )
}

const EVENT_STYLE: Record<LabEntry['event'], string> = {
  model: 'text-muted', tool_call: 'text-accent', tool_result: 'text-success', rejected: 'text-warning',
  output: 'text-foreground font-semibold', error: 'text-danger',
}

function LogLine({ entry }: { entry: LabEntry }) {
  const sub = entry.agent !== 'coordinator'
  const failed = entry.event === 'tool_result' && (entry.data as { status?: string })?.status === 'error'
  return (
    <details className={`rounded px-1 hover:bg-default ${sub ? 'ml-6' : ''}`}>
      <summary className="cursor-pointer list-none">
        <span className="text-muted">{new Date(entry.at).toLocaleTimeString('en-GB', { timeZone: 'UTC' })}</span>{' '}
        {!sub && <span className="font-sans font-medium">{entry.member}</span>}{' '}
        <span className="text-muted">{sub ? `↳ ${entry.agent}` : ''}</span>{' '}
        <span className={failed ? 'text-danger' : EVENT_STYLE[entry.event]}>{entry.event}</span>{' '}
        {entry.name && <span>{entry.name}</span>}{' '}
        <span className="text-muted">{summary(entry)}</span>
      </summary>
      <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-default p-2">
        {entry.prompt_id && `${entry.prompt_id}\n`}{pretty(entry.data)}
      </pre>
    </details>
  )
}

/** One line about a step: a model call's time and tokens, a tool's arguments or result, a reply. */
function summary(entry: LabEntry): string {
  const data = entry.data as Record<string, unknown> | string | null
  if (entry.event === 'model' && data && typeof data === 'object') {
    return `${data.ms} ms · ${data.input_tokens} in · ${data.output_tokens} out · ${data.tool_calls} tool calls`
  }
  if (entry.event === 'tool_result' && data && typeof data === 'object') {
    return `${data.ms} ms · ${short(String(data.content))}`
  }
  return short(typeof data === 'string' ? data : JSON.stringify(data))
}

const short = (text: string, limit = 140) => (text.length > limit ? `${text.slice(0, limit)}…` : text)

/** The data as readable JSON: a tool result's content is itself JSON, so it's unpacked too. */
function pretty(data: unknown): string {
  if (data && typeof data === 'object' && 'content' in data && typeof data.content === 'string') {
    try {
      return JSON.stringify({ ...data, content: JSON.parse(data.content) }, null, 2)
    } catch { /* plain text content */ }
  }
  return typeof data === 'string' ? data : JSON.stringify(data, null, 2)
}
