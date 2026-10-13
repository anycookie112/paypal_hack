import { Button, Card, Switch, toast } from '@heroui/react'
import { useEffect, useState } from 'react'
import { api, dollars, money, toCents } from '../api'
import type { GroupSettings, SettingValue, SettingView } from '../api'
import { Choice, ErrorAlert, Field, Loading, PageTitle } from '../components/ui'
import { useMember } from '../hooks'

const CHOICE_LABEL: Record<string, string> = { each: 'Every purchase', daily: 'A daily summary' }

/** The group's name, budget and rules (docs/SPEC.md §5). Everyone sees them; the treasurer changes them. */
export default function Settings() {
  const { me } = useMember()
  const [saved, setSaved] = useState<GroupSettings | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.settings().then(setSaved).catch((e: Error) => setError(e.message))
  }, [])

  if (error) return <ErrorAlert title="Couldn't load the settings" message={error} />
  if (!saved) return <Loading />
  return me?.role === 'admin' ? <SettingsForm saved={saved} onSaved={setSaved} /> : <SettingsList saved={saved} />
}

function shown(setting: SettingView, value: SettingValue = setting.value): string {
  if (setting.kind === 'cents') return money(value as number)
  if (setting.kind === 'bool') return value ? 'Yes' : 'No'
  if (setting.kind === 'choice') return CHOICE_LABEL[value as string] ?? String(value)
  return String(value)
}

function SettingsList({ saved }: { saved: GroupSettings }) {
  const rows = [
    { label: 'Group', value: saved.name },
    { label: 'Monthly budget', value: saved.monthly_budget_cents ? money(saved.monthly_budget_cents) : 'None' },
    ...saved.settings.map((s) => ({ label: s.label, value: shown(s) })),
  ]
  return (
    <>
      <PageTitle title="Settings" subtitle="The group's rules. Only the treasurer can change them." />
      <Card>
        <Card.Content>
          <dl className="divide-y divide-border">
            {rows.map(({ label, value }) => (
              <div key={label} className="flex flex-wrap justify-between gap-x-6 gap-y-1 py-3">
                <dt className="text-muted">{label}</dt>
                <dd className="font-medium">{value}</dd>
              </div>
            ))}
          </dl>
        </Card.Content>
      </Card>
    </>
  )
}

/** What's typed in a box, before it's checked: amounts and numbers stay text until Save. */
type Draft = Record<string, string | boolean>

function draftOf(saved: GroupSettings): Draft {
  return Object.fromEntries(saved.settings.map((s) => [
    s.key, s.kind === 'bool' ? Boolean(s.value) : s.kind === 'cents' ? dollars(s.value as number) : String(s.value),
  ]))
}

function SettingsForm({ saved, onSaved }: { saved: GroupSettings; onSaved: (s: GroupSettings) => void }) {
  const [name, setName] = useState(saved.name)
  const [budget, setBudget] = useState(saved.monthly_budget_cents ? dollars(saved.monthly_budget_cents) : '')
  const [draft, setDraft] = useState<Draft>(() => draftOf(saved))
  const [problem, setProblem] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const set = (key: string, value: string | boolean) => setDraft((d) => ({ ...d, [key]: value }))

  /** The changes, or a message about the first box that isn't valid. */
  const changes = (): { values: Record<string, SettingValue>; budgetCents: number } | string => {
    const budgetCents = budget.trim() ? toCents(budget) : 0
    if (budgetCents === null) return 'Enter the monthly budget as an amount, like 300, or leave it empty for none.'
    const values: Record<string, SettingValue> = {}
    for (const s of saved.settings) {
      const typed = draft[s.key]
      let value: SettingValue = typed
      if (s.kind === 'cents') {
        const cents = toCents(String(typed))
        if (cents === null) return `${s.label}: enter an amount, like 100.`
        value = cents
      } else if (s.kind === 'int') {
        value = Number(typed)
        if (!Number.isInteger(value)) return `${s.label}: enter a whole number.`
      }
      if (value !== s.value) values[s.key] = value
    }
    return { values, budgetCents }
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    const found = changes()
    if (typeof found === 'string') return setProblem(found)
    setSaving(true)
    try {
      const next = await api.saveSettings({ name, monthly_budget_cents: found.budgetCents, values: found.values })
      onSaved(next)
      setDraft(draftOf(next))
      setProblem(null)
      toast.success('Settings saved.')
    } catch (err) {
      setProblem((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={save}>
      <PageTitle title="Settings" subtitle="The group's rules, checked by code on every request. Changes apply from now on." />
      <div className="flex flex-col gap-6">
        <Card>
          <Card.Header><Card.Title>The group</Card.Title></Card.Header>
          <Card.Content className="grid gap-4 sm:grid-cols-2">
            <Field label="Name" value={name} onChange={setName} isRequired />
            <Field label="Monthly budget ($)" value={budget} onChange={setBudget} inputMode="decimal"
                   description="Everything together. Categories can have their own, on the Items page." />
          </Card.Content>
        </Card>
        <Card>
          <Card.Header><Card.Title>Rules</Card.Title></Card.Header>
          <Card.Content className="flex flex-col gap-5">
            {saved.settings.map((s) => <SettingField key={s.key} setting={s} value={draft[s.key]}
                                                     onChange={(v) => set(s.key, v)} />)}
          </Card.Content>
        </Card>
        <ErrorAlert title="Not saved" message={problem} />
        <div><Button type="submit" isDisabled={saving}>{saving ? 'Saving…' : 'Save settings'}</Button></div>
      </div>
    </form>
  )
}

function SettingField({ setting, value, onChange }: {
  setting: SettingView
  value: string | boolean
  onChange: (value: string | boolean) => void
}) {
  const fallback = `Default: ${shown(setting, setting.default)}`
  if (setting.kind === 'bool') {
    return (
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-sm font-medium">{setting.label}</div>
          <div className="text-xs text-muted">{fallback}</div>
        </div>
        <Switch isSelected={Boolean(value)} onChange={onChange} aria-label={setting.label}>
          <Switch.Control><Switch.Thumb /></Switch.Control>
        </Switch>
      </div>
    )
  }
  if (setting.kind === 'choice') {
    return <Choice label={setting.label} value={String(value)} onChange={onChange}
                   options={setting.choices.map((c) => ({ id: c, label: CHOICE_LABEL[c] ?? c }))} />
  }
  const range = setting.kind === 'cents'
    ? `${money(setting.low)} to ${money(setting.high)}`
    : `${setting.low} to ${setting.high}`
  return (
    <Field label={setting.kind === 'cents' ? `${setting.label} ($)` : setting.label} value={String(value)}
           onChange={onChange} inputMode={setting.kind === 'cents' ? 'decimal' : 'numeric'}
           description={`${range}. ${fallback}.`} />
  )
}
