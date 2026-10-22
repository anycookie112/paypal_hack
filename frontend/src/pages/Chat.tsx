import { Button, Card, Spinner, TextArea, TextField } from '@heroui/react'
import { useEffect, useRef, useState } from 'react'
import { api, say } from '../api'
import type { ChatMessage } from '../api'
import { ErrorAlert, Loading, PageTitle, SlowNote } from '../components/ui'

const EXAMPLES = ["We're out of gloves, I'll grab some", 'How much can I still spend this month?',
                  "The coffee isn't in the cupboard"]
const stamp = () => new Date().toISOString()

/** Tell the AI before you buy. It checks the list and who's on it; the app's rules decide. */
export default function Chat() {
  const [messages, setMessages] = useState<ChatMessage[] | null>(null)
  const [available, setAvailable] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [reply, setReply] = useState<{ text: string; step: string } | null>(null)  // being written
  const end = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.chat().then((c) => { setMessages(c.messages); setAvailable(c.ai_available) })
      .catch((e: Error) => setError(e.message))
  }, [])
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [messages, reply])

  const send = async (text: string) => {
    text = text.trim()
    if (!text || reply) return
    setMessages((m) => [...(m ?? []), { role: 'user', text, created_at: stamp() }])
    setDraft('')
    setError(null)
    setReply({ text: '', step: '' })
    try {
      await say(text, (event) => {
        if (event.type === 'step') setReply((r) => r && { ...r, step: event.text })
        else if (event.type === 'text') setReply((r) => r && { ...r, text: r.text + event.text })
        else setMessages((m) => [...(m ?? []), { role: 'assistant', text: event.text, created_at: stamp() }])
      })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setReply(null)
    }
  }

  if (!messages && !error) return <Loading />
  return (
    <>
      <PageTitle title="Chat" subtitle="Tell the AI before you buy for the group, so two people don't get the same thing." />
      {!available && <ErrorAlert title="The AI isn't set up" message="Set LLM_API_KEY on the server to chat." />}
      <Card>
        <Card.Content className="flex flex-col gap-4">
          <div className="flex max-h-[60vh] min-h-48 flex-col gap-3 overflow-y-auto pr-1">
            {messages?.length === 0 && !reply && (
              <div className="flex flex-col items-start gap-2">
                <p className="text-sm text-muted">Try:</p>
                {EXAMPLES.map((e) => (
                  <Button key={e} size="sm" variant="secondary" isDisabled={!available} onPress={() => send(e)}>{e}</Button>
                ))}
              </div>
            )}
            {messages?.map((m, i) => <Bubble key={i} role={m.role} text={m.text} />)}
            {reply && (reply.text
              ? <Bubble role="assistant" text={reply.text} />
              : <div className="flex items-center gap-2 text-sm text-muted"><Spinner size="sm" /> {reply.step || 'Thinking…'}</div>)}
            <div ref={end} />
          </div>
          <ErrorAlert title="Not sent" message={error} />
          <SlowNote active={!!reply} />
          <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); send(draft) }}>
            <TextField aria-label="Message" value={draft} onChange={setDraft} fullWidth className="grow">
              <TextArea rows={2} placeholder="We're out of gloves, I'll grab some by 11"
                        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(draft) } }} />
            </TextField>
            <Button type="submit" isDisabled={!draft.trim() || !!reply || !available}>Send</Button>
          </form>
        </Card.Content>
      </Card>
    </>
  )
}

export function Bubble({ role, text }: { role: 'user' | 'assistant'; text: string }) {
  const mine = role === 'user'
  return (
    <div className={`flex ${mine ? 'justify-end' : 'justify-start'}`}>
      <p className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm ${mine ? 'bg-accent text-accent-foreground' : 'bg-default'}`}>
        {text}
      </p>
    </div>
  )
}
