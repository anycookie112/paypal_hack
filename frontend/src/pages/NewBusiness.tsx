import { Button, Card, Form } from '@heroui/react'
import { useState } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../api'
import { SetupSteps } from '../components/SetupSteps'
import { AreaField, ErrorAlert, Field, PageTitle } from '../components/ui'
import { useMeta } from '../hooks'

export default function NewBusiness() {
  const navigate = useNavigate()
  const meta = useMeta()
  const [form, setForm] = useState({ name: '', kind: '', description: '', paypal_email: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (key: keyof typeof form) => (value: string) => setForm((f) => ({ ...f, [key]: value }))

  async function submit() {
    setSaving(true)
    setError(null)
    try {
      const business = await api.createBusiness(form)
      navigate(`/b/${business.slug}/setup/products`)
    } catch (e) {
      setError((e as Error).message)
      setSaving(false)
    }
  }

  return (
    <>
      <SetupSteps current="profile" />
      <PageTitle title="Tell us about your business" />
      <Card className="max-w-xl">
        <Card.Content>
          <Form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); submit() }}>
            <Field label="Business name" value={form.name} onChange={set('name')} isRequired
                   placeholder="Sweet Crumb Bakery" />
            <Field label="What kind of business?" value={form.kind} onChange={set('kind')}
                   placeholder="bakery" list="business-kinds" />
            <datalist id="business-kinds">
              {meta?.business_kinds.map((k) => <option key={k} value={k} />)}
            </datalist>
            <AreaField label="Short description" value={form.description} onChange={set('description')} rows={2}
                       description="Shown to your customers." placeholder="Family bakery in Cincinnati since 1998" />
            <Field label="PayPal email" type="email" value={form.paypal_email} onChange={set('paypal_email')}
                   isRequired description="Where your payments go." />
            <ErrorAlert message={error} />
            <Button type="submit" isPending={saving} className="self-start">Continue to products</Button>
          </Form>
        </Card.Content>
      </Card>
    </>
  )
}
