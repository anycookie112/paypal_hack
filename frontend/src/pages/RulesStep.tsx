import { Alert, Button, Card, Checkbox, Chip, toast } from '@heroui/react'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { api } from '../api'
import type { BusinessDetail, Meta, Rule, RuleDraft } from '../api'
import { SetupSteps } from '../components/SetupSteps'
import { AreaField, Choice, ErrorAlert, Field, Loading, PageTitle, SlowNote } from '../components/ui'
import { useBusiness, useMeta } from '../hooks'

type Form = Record<string, string>

interface KindInfo {
  kind: string
  title: string
  description: string
  /** What applies when there's no rule of this kind. */
  off: string
  /** Form values used when the owner turns this setting on. */
  start: Form
}

const KINDS: KindInfo[] = [
  { kind: 'payment', title: 'How customers pay', off: 'Paid in full when ordering',
    description: 'PayPal charges in full, holds then charges the exact amount, or takes a deposit.',
    start: { style: 'pay_now', deposit_percent: '50', buffer_percent: '10' } },
  { kind: 'lead_time', title: 'Notice needed', off: 'No notice needed',
    description: "How far ahead customers must order. Products can also set their own notice.",
    start: { notice: '1', unit: 'days' } },
  { kind: 'min_order', title: 'Minimum order', off: 'No minimum', description: 'The smallest order you accept.',
    start: { amount: '20' } },
  { kind: 'delivery', title: 'Delivery', off: 'Pickup only', description: 'Where you deliver and what it costs.',
    start: { radius_miles: '5', fee: '5', free_over: '', min_order: '' } },
  { kind: 'capacity', title: 'Daily limit', off: 'No daily limit', description: 'The most you can make or supply per day.',
    start: { max_per_day: '10' } },
  { kind: 'substitution', title: 'Substitutions', off: 'No substitutions', description: 'When something is out of stock.',
    start: { allowed: 'yes' } },
]

const dollars = (cents: unknown) => (typeof cents === 'number' ? String(cents / 100) : '')

/** A saved rule's params as form strings. */
function toForm(kind: string, params: Record<string, unknown>): Form {
  const start = KINDS.find((k) => k.kind === kind)?.start ?? {}
  switch (kind) {
    case 'payment':
      return { ...start, style: String(params.style),
               ...(params.deposit_percent != null && { deposit_percent: String(params.deposit_percent) }),
               ...(params.buffer_percent != null && { buffer_percent: String(params.buffer_percent) }) }
    case 'lead_time': {
      const hours = Number(params.hours)
      return hours > 0 && hours % 24 === 0
        ? { notice: String(hours / 24), unit: 'days' } : { notice: String(hours), unit: 'hours' }
    }
    case 'min_order':
      return { amount: dollars(params.amount_cents) }
    case 'delivery':
      return { radius_miles: String(params.radius_miles), fee: dollars(params.fee_cents),
               free_over: dollars(params.free_over_cents), min_order: dollars(params.min_order_cents) }
    case 'capacity':
      return { max_per_day: String(params.max_per_day) }
    case 'substitution':
      return { allowed: params.allowed ? 'yes' : 'no' }
    default:
      return {}
  }
}

/** Form strings back to params. The server validates them and says what's wrong. */
function toParams(kind: string, f: Form): Record<string, unknown> {
  switch (kind) {
    case 'payment':
      return { style: f.style,
               ...(f.style === 'deposit_balance' && { deposit_percent: f.deposit_percent }),
               ...(f.style === 'hold_exact' && { buffer_percent: f.buffer_percent }) }
    case 'lead_time':
      return { hours: f.unit === 'days' ? String(Number(f.notice) * 24) : f.notice }
    case 'min_order':
      return { amount: f.amount }
    case 'delivery':
      return { radius_miles: f.radius_miles, fee: f.fee, free_over: f.free_over, min_order: f.min_order }
    case 'capacity':
      return { max_per_day: f.max_per_day }
    case 'substitution':
      return { allowed: f.allowed === 'yes' }
    default:
      return {}
  }
}

const kindLabel = (kind: string) => kind.replace('_', ' ')

function Origin({ text }: { text: string }) {
  if (!text) return null
  return <span className="text-xs text-muted">{text.startsWith('Default') ? text : `From: "${text}"`}</span>
}

export default function RulesStep() {
  const { slug = '' } = useParams()
  const navigate = useNavigate()
  const meta = useMeta()
  const { detail, error, reload } = useBusiness(slug)
  const [resetting, setResetting] = useState(false)

  if (error) return <ErrorAlert message={error} />
  if (!detail || !meta) return <Loading />

  async function reset() {
    if (!window.confirm('Remove all your rules and go back to the defaults?')) return
    setResetting(true)
    try {
      await api.resetRules(slug)
      await reload()
      toast.success('Rules reset to defaults')
    } finally {
      setResetting(false)
    }
  }

  const kindName = detail.business.kind.trim().toLowerCase()

  return (
    <>
      <SetupSteps current="rules" slug={slug} />
      <PageTitle title={`${detail.business.name}: rules & payments`}
                 subtitle={`We've started you with common settings${kindName ? ` for a ${kindName}` : ''}. Change anything that doesn't fit; each setting can be different for a category.`} />

      <div className="grid gap-4 lg:grid-cols-2">
        {KINDS.map((info) => (
          <KindCard key={info.kind} slug={slug} info={info} meta={meta} detail={detail} onChanged={reload} />
        ))}
      </div>

      <AdvancedSection slug={slug} meta={meta} notes={detail.rules.filter((r) => r.kind === 'note')} onChanged={reload} />

      <div className="mt-8 flex flex-wrap justify-between gap-3">
        <div className="flex gap-3">
          <Button variant="secondary" onPress={() => navigate(`/b/${slug}/setup/products`)}>Back</Button>
          <Button variant="ghost" isPending={resetting} onPress={reset}>Reset to defaults</Button>
        </div>
        <Button onPress={() => navigate(`/b/${slug}/setup/done`)}>Finish</Button>
      </div>
    </>
  )
}

/** One rule kind: the setting for all products, plus any per-category overrides. */
function KindCard({ slug, info, meta, detail, onChanged }: {
  slug: string
  info: KindInfo
  meta: Meta
  detail: BusinessDetail
  onChanged: () => Promise<void>
}) {
  // applies_to of the row being edited ('' = all products); `adding` = a new category override.
  const [editing, setEditing] = useState<{ appliesTo: string; form: Form; adding?: boolean } | null>(null)
  const [removing, setRemoving] = useState<number | null>(null)

  const rules = detail.rules.filter((r) => r.kind === info.kind)
  const general = rules.find((r) => !r.applies_to)
  const overrides = rules.filter((r) => r.applies_to)
  const covered = new Set(overrides.flatMap((r) => r.applies_to.split(',').map((c) => c.trim().toLowerCase())))
  const freeCategories = detail.categories.filter((c) => !covered.has(c.toLowerCase()))

  async function remove(rule: Rule) {
    setRemoving(rule.id)
    try {
      await api.deleteRule(slug, rule.id)
      await onChanged()
    } finally {
      setRemoving(null)
    }
  }

  const editor = (appliesTo: string) => editing && editing.appliesTo === appliesTo && !editing.adding && (
    <RuleEditor slug={slug} kind={info.kind} meta={meta} appliesTo={appliesTo} initial={editing.form}
                onCancel={() => setEditing(null)}
                onSaved={async () => { setEditing(null); await onChanged() }} />
  )

  return (
    <Card>
      <Card.Header>
        <Card.Title>{info.title}</Card.Title>
        <Card.Description>{info.description}</Card.Description>
      </Card.Header>
      <Card.Content className="flex flex-col gap-3">
        <SettingRow label="All products" text={general ? capitalize(general.setting) : info.off}
                    origin={general?.source_text ?? ''} isOff={!general}
                    actions={editing?.appliesTo === '' && !editing.adding ? null : (
                      <>
                        <Button size="sm" variant="secondary"
                                onPress={() => setEditing({ appliesTo: '', form: general ? toForm(info.kind, general.params) : info.start })}>
                          {general ? 'Change' : 'Turn on'}
                        </Button>
                        {general && info.kind !== 'payment' && (
                          <Button size="sm" variant="ghost" isPending={removing === general.id} onPress={() => remove(general)}>
                            Turn off
                          </Button>
                        )}
                      </>
                    )} />
        {editor('')}

        {overrides.map((rule) => (
          <div key={rule.id} className="flex flex-col gap-3">
            <SettingRow label={rule.applies_to} text={capitalize(rule.setting)} origin={rule.source_text}
                        actions={editing?.appliesTo === rule.applies_to && !editing.adding ? null : (
                          <>
                            <Button size="sm" variant="secondary"
                                    onPress={() => setEditing({ appliesTo: rule.applies_to, form: toForm(info.kind, rule.params) })}>
                              Change
                            </Button>
                            <Button size="sm" variant="ghost" isPending={removing === rule.id} onPress={() => remove(rule)}>
                              Remove
                            </Button>
                          </>
                        )} />
            {editor(rule.applies_to)}
          </div>
        ))}

        {info.kind === 'payment' && <PaymentHint detail={detail} />}

        {editing?.adding ? (
          <RuleEditor slug={slug} kind={info.kind} meta={meta} categories={freeCategories} initial={editing.form}
                      onCancel={() => setEditing(null)}
                      onSaved={async () => { setEditing(null); await onChanged() }} />
        ) : freeCategories.length > 0 && (
          <Button size="sm" variant="ghost" className="self-start"
                  onPress={() => setEditing({ appliesTo: freeCategories[0], form: info.start, adding: true })}>
            + Different for a category
          </Button>
        )}
      </Card.Content>
    </Card>
  )
}

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)

function SettingRow({ label, text, origin, isOff, actions }: {
  label: string
  text: string
  origin: string
  isOff?: boolean
  actions: React.ReactNode
}) {
  return (
    <div className="flex flex-row items-start justify-between gap-3 rounded-xl border border-border p-3">
      <div className="flex flex-col gap-1">
        <span className="text-sm font-medium">{label}</span>
        <span className={isOff ? 'text-muted' : ''}>{text}</span>
        <Origin text={origin} />
      </div>
      <div className="flex shrink-0 gap-1">{actions}</div>
    </div>
  )
}

/** Points out categories sold by weight or on request that are still charged in full up front. */
function PaymentHint({ detail }: { detail: BusinessDetail }) {
  const variable = new Set(detail.products.filter((p) => p.pricing !== 'fixed').map((p) => p.category))
  const misfits = detail.payment_by_category
    .filter((p) => p.style === 'pay_now' && variable.has(p.category))
    .map((p) => p.category)
  if (misfits.length === 0) return null
  return (
    <Alert status="warning">
      <Alert.Indicator />
      <Alert.Content>
        <Alert.Description>
          Some products in {misfits.join(', ')} are sold by weight or priced on request, but customers are charged
          in full when ordering. "Hold, then charge the exact amount" fits them better.
        </Alert.Description>
      </Alert.Content>
    </Alert>
  )
}

/** Fixed fields for one rule kind. Saving replaces the current setting for the same scope. */
function RuleEditor({ slug, kind, meta, appliesTo, categories, initial, onCancel, onSaved }: {
  slug: string
  kind: string
  meta: Meta
  appliesTo?: string
  categories?: string[]
  initial: Form
  onCancel: () => void
  onSaved: () => Promise<void>
}) {
  const [form, setForm] = useState<Form>(initial)
  const [scope, setScope] = useState(appliesTo ?? categories?.[0] ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (key: string) => (value: string) => setForm((f) => ({ ...f, [key]: value }))

  async function save() {
    setSaving(true)
    setError(null)
    try {
      await api.setRule(slug, { kind, applies_to: scope, params: toParams(kind, form) })
      toast.success('Saved')
      await onSaved()
    } catch (e) {
      setError((e as Error).message)
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-accent p-3">
      {categories && (
        <Choice label="Category" value={scope} onChange={setScope}
                options={categories.map((c) => ({ id: c, label: c }))} />
      )}
      {kind === 'payment' && (
        <>
          <Choice label="Payment style" value={form.style} onChange={set('style')}
                  options={Object.entries(meta.payment_styles).map(([id, label]) => ({ id, label }))} />
          {form.style === 'deposit_balance' && (
            <Field label="Deposit %" value={form.deposit_percent} onChange={set('deposit_percent')} inputMode="numeric" />
          )}
          {form.style === 'hold_exact' && (
            <Field label="Extra to hold %" value={form.buffer_percent} onChange={set('buffer_percent')} inputMode="numeric"
                   description="Covers weight differences or substitutions. The unused part is released. Use 0 for quotes." />
          )}
        </>
      )}
      {kind === 'lead_time' && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="Notice" value={form.notice} onChange={set('notice')} inputMode="numeric" />
          <Choice label="Unit" value={form.unit} onChange={set('unit')}
                  options={[{ id: 'hours', label: 'hours' }, { id: 'days', label: 'days' }]} />
        </div>
      )}
      {kind === 'min_order' && (
        <Field label="Minimum order ($)" value={form.amount} onChange={set('amount')} inputMode="decimal" />
      )}
      {kind === 'delivery' && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="Within (miles)" value={form.radius_miles} onChange={set('radius_miles')} inputMode="decimal" />
          <Field label="Fee ($)" value={form.fee} onChange={set('fee')} inputMode="decimal" />
          <Field label="Free over ($)" value={form.free_over} onChange={set('free_over')} inputMode="decimal"
                 description="Optional" />
          <Field label="Minimum for delivery ($)" value={form.min_order} onChange={set('min_order')} inputMode="decimal"
                 description="Optional" />
        </div>
      )}
      {kind === 'capacity' && (
        <Field label="Most per day" value={form.max_per_day} onChange={set('max_per_day')} inputMode="numeric" />
      )}
      {kind === 'substitution' && (
        <Choice label="If something is out of stock" value={form.allowed} onChange={set('allowed')}
                options={[{ id: 'yes', label: 'Substitute something similar' },
                          { id: 'no', label: "Don't substitute; tell the customer" }]} />
      )}
      <ErrorAlert message={error} />
      <div className="flex gap-2">
        <Button size="sm" isPending={saving} onPress={save}>Save</Button>
        <Button size="sm" variant="secondary" onPress={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

/** Guidance for the assistant that code doesn't enforce. */
function NotesCard({ slug, notes, onChanged }: { slug: string; notes: Rule[]; onChanged: () => Promise<void> }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function add() {
    setBusy(true)
    setError(null)
    try {
      await api.setRule(slug, { kind: 'note', applies_to: '', params: { text } })
      setText('')
      await onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <Card.Header>
        <Card.Title>Notes for the assistant</Card.Title>
        <Card.Description>
          Anything else it should know when helping customers. These guide the assistant but aren't enforced.
        </Card.Description>
      </Card.Header>
      <Card.Content className="flex flex-col gap-3">
        {notes.map((n) => (
          <SettingRow key={n.id} label={n.applies_to || 'All products'} text={String(n.params.text)} origin={n.source_text}
                      actions={<Button size="sm" variant="ghost" onPress={async () => {
                        await api.deleteRule(slug, n.id)
                        await onChanged()
                      }}>Remove</Button>} />
        ))}
        <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-end">
          <Field label="New note" value={text} onChange={setText} placeholder="Suggest our seasonal pies in autumn" />
          <Button isDisabled={!text.trim()} isPending={busy} onPress={add}>Add note</Button>
        </div>
        <ErrorAlert message={error} />
      </Card.Content>
    </Card>
  )
}

/** Free text, for owners comfortable with AI tools. Hidden by default: without it, the assistant works
 *  only from products, their descriptions and the settings above. */
function AdvancedSection({ slug, meta, notes, onChanged }: {
  slug: string
  meta: Meta
  notes: Rule[]
  onChanged: () => Promise<void>
}) {
  const [open, setOpen] = useState(notes.length > 0)
  const [drafts, setDrafts] = useState<(RuleDraft & { keep: boolean })[] | null>(null)

  return (
    <section className="mt-8">
      <Button variant="ghost" onPress={() => setOpen(!open)}>
        {open ? '▾' : '▸'} Advanced: plain-English policies and notes (AI)
      </Button>
      {open && (
        <div className="mt-3 flex flex-col gap-4">
          <p className="text-sm text-muted">
            Optional. The assistant already works from your products, their descriptions and the settings above.
            Use this only if you're comfortable checking what an AI understood.
          </p>
          {drafts ? (
            <ReviewRules slug={slug} drafts={drafts} onChange={setDrafts}
                         onDone={async (saved) => {
                           setDrafts(null)
                           await onChanged()
                           toast.success(`Saved ${saved} rule${saved === 1 ? '' : 's'}`)
                         }} />
          ) : (
            <PoliciesForm slug={slug} meta={meta} onDrafts={(d) => setDrafts(d.map((r) => ({ ...r, keep: !r.error })))} />
          )}
          <NotesCard slug={slug} notes={notes} onChanged={onChanged} />
        </div>
      )}
    </section>
  )
}

function PoliciesForm({ slug, meta, onDrafts }: { slug: string; meta: Meta; onDrafts: (d: RuleDraft[]) => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function convert() {
    setBusy(true)
    setError(null)
    try {
      onDrafts((await api.draftRules(slug, text)).drafts)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <Card.Header>
        <Card.Title>Describe your policies</Card.Title>
        <Card.Description>
          Write them the way you'd explain them to a new employee. The AI turns them into the settings above;
          you check each one before it's saved.
        </Card.Description>
      </Card.Header>
      <Card.Content className="flex flex-col items-start gap-3">
        {meta.ai_available ? (
          <>
            <AreaField label="Policies" value={text} onChange={setText} rows={7}
                       placeholder={'Custom cakes need 3 days notice and a 50% deposit.\nWe deliver within 5 miles for $8, free over $75.\nMax 10 custom cakes a day.\nNo substitutions on cakes.'} />
            <Button isDisabled={!text.trim()} isPending={busy} onPress={convert}>Turn into rules</Button>
            <SlowNote active={busy} />
          </>
        ) : (
          <Alert status="warning">
            <Alert.Indicator />
            <Alert.Content>
              <Alert.Title>AI isn't configured</Alert.Title>
              <Alert.Description>Set LLM_API_KEY on the server. You can still use the settings above.</Alert.Description>
            </Alert.Content>
          </Alert>
        )}
        <ErrorAlert message={error} />
      </Card.Content>
    </Card>
  )
}

function ReviewRules({ slug, drafts, onChange, onDone }: {
  slug: string
  drafts: (RuleDraft & { keep: boolean })[]
  onChange: (d: (RuleDraft & { keep: boolean })[] | null) => void
  onDone: (saved: number) => Promise<void>
}) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const kept = drafts.filter((d) => d.keep && d.rule)

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const result = await api.saveRules(slug, kept.map((d) => d.rule!))
      if (result.rejected.length) toast.warning(`${result.rejected.length} rule(s) were rejected`)
      await onDone(result.saved)
    } catch (e) {
      setError((e as Error).message)
      setSaving(false)
    }
  }

  return (
    <Card>
      <Card.Header>
        <Card.Title>Check your rules</Card.Title>
        <Card.Description>
          Each card says exactly what the system will enforce, and replaces your current setting of the same kind.
          Untick anything that's wrong; you can rephrase and try again.
        </Card.Description>
      </Card.Header>
      <Card.Content className="flex flex-col gap-2">
        {drafts.map((d, i) => (
          <div key={i}
               className={`flex items-start gap-3 rounded-xl border p-3 ${d.error ? 'border-danger bg-danger-soft' : 'border-border'}`}>
            {d.rule ? (
              <Checkbox aria-label="Keep this rule" variant="secondary" isSelected={d.keep}
                        onChange={(keep) => onChange(drafts.map((x, j) => (j === i ? { ...x, keep } : x)))}>
                <Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control></Checkbox.Content>
              </Checkbox>
            ) : <span className="w-5" />}
            <div className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                <Chip size="sm" color={d.error ? 'danger' : 'accent'} variant="soft">
                  {d.rule ? kindLabel(d.rule.kind) : 'not understood'}
                </Chip>
                <span>{d.summary ?? `${d.error}. This one won't be saved; rephrase it and try again.`}</span>
              </div>
              {d.source_text && <span className="text-xs text-muted">From: "{d.source_text}"</span>}
            </div>
          </div>
        ))}
        <ErrorAlert message={error} />
      </Card.Content>
      <Card.Footer className="flex justify-between gap-3">
        <Button variant="secondary" onPress={() => onChange(null)}>Cancel</Button>
        <Button isDisabled={kept.length === 0} isPending={saving} onPress={save}>
          Save {kept.length} rule{kept.length === 1 ? '' : 's'}
        </Button>
      </Card.Footer>
    </Card>
  )
}
