import { Alert, Button, Card, Checkbox, Chip, Input, Table, Tabs, TextField, toast } from '@heroui/react'
import type { Key } from '@heroui/react'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { ApiError, api, emptyProduct, money } from '../api'
import type { Meta, Product, ProductDraft, ProductFields } from '../api'
import { SetupSteps } from '../components/SetupSteps'
import { AreaField, Choice, ErrorAlert, Field, Loading, PageTitle, SlowNote } from '../components/ui'
import { useBusiness, useMeta } from '../hooks'

interface ReviewRow extends ProductDraft {
  key: number
  keep: boolean
}

export default function ProductsStep() {
  const { slug = '' } = useParams()
  const navigate = useNavigate()
  const meta = useMeta()
  const { detail, error, reload } = useBusiness(slug)
  const [review, setReview] = useState<{ rows: ReviewRow[]; source: string } | null>(null)

  if (error) return <ErrorAlert message={error} />
  if (!detail || !meta) return <Loading />

  const startReview = (drafts: ProductDraft[], source: string) =>
    setReview({ rows: drafts.map((d, i) => ({ ...d, key: i, keep: true })), source })

  return (
    <>
      <SetupSteps current="products" slug={slug} />
      <PageTitle title={`${detail.business.name}: products`}
                 subtitle="Add what you sell. Everything is shown for review before it's saved." />

      {review ? (
        <ReviewProducts slug={slug} meta={meta} review={review} onChange={setReview}
                        onDone={async (saved) => {
                          setReview(null)
                          await reload()
                          toast.success(`Saved ${saved} product${saved === 1 ? '' : 's'}`)
                        }} />
      ) : (
        <ImportProducts slug={slug} meta={meta} onDrafts={startReview} onAdded={reload} />
      )}

      <section className="mt-10">
        <h2 className="mb-3 text-lg font-semibold">
          Your products <span className="font-normal text-muted">({detail.products.length})</span>
        </h2>
        {detail.products.length === 0
          ? <p className="text-muted">No products yet.</p>
          : <ProductList slug={slug} meta={meta} products={detail.products} onRemoved={reload} />}
      </section>

      <div className="mt-8 flex justify-end">
        <Button onPress={() => navigate(`/b/${slug}/setup/rules`)}>Next: rules &amp; payments</Button>
      </div>
    </>
  )
}

function ImportProducts({ slug, meta, onDrafts, onAdded }: {
  slug: string
  meta: Meta
  onDrafts: (drafts: ProductDraft[], source: string) => void
  onAdded: () => Promise<void>
}) {
  const [tab, setTab] = useState<Key>('csv')
  const [file, setFile] = useState<File | null>(null)
  const [text, setText] = useState('')
  const [manual, setManual] = useState<ProductFields>(emptyProduct())
  const [busy, setBusy] = useState<'csv' | 'text' | 'manual' | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function run(kind: 'csv' | 'text' | 'manual', action: () => Promise<void>) {
    setBusy(kind)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  const setManualField = (key: keyof ProductFields) => (value: string) => setManual((m) => ({ ...m, [key]: value }))

  return (
    <Card>
      <Card.Content className="flex flex-col gap-4">
        <Tabs selectedKey={tab} onSelectionChange={(k) => { setTab(k); setError(null) }}>
          <Tabs.ListContainer>
            <Tabs.List aria-label="Ways to add products">
              <Tabs.Tab id="csv">Upload spreadsheet<Tabs.Indicator /></Tabs.Tab>
              <Tabs.Tab id="text">Paste a menu<Tabs.Indicator /></Tabs.Tab>
              <Tabs.Tab id="manual">Add one<Tabs.Indicator /></Tabs.Tab>
            </Tabs.List>
          </Tabs.ListContainer>

          <Tabs.Panel id="csv" className="flex flex-col items-start gap-3 pt-4">
            <p className="text-sm text-muted">
              A CSV file with columns {meta.csv_columns.join(', ')}.{' '}
              <a className="link" href="/api/products-template.csv">Download the template</a>.
            </p>
            <input
              type="file" accept=".csv,text/csv" aria-label="Product spreadsheet"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-accent file:px-3 file:py-1.5 file:text-accent-foreground"
            />
            <Button isDisabled={!file} isPending={busy === 'csv'}
                    onPress={() => run('csv', async () => onDrafts((await api.importCsv(slug, file!)).drafts, 'your spreadsheet'))}>
              Review import
            </Button>
          </Tabs.Panel>

          <Tabs.Panel id="text" className="flex flex-col items-start gap-3 pt-4">
            {meta.ai_available ? (
              <>
                <AreaField label="Your menu or price list" value={text} onChange={setText} rows={7}
                           description="Any format. The AI turns it into products for you to check."
                           placeholder={'Sourdough loaf $6.50\nCupcakes $3 each, nut-free\nCustom cakes from $36, 3 days notice'} />
                <Button isDisabled={!text.trim()} isPending={busy === 'text'}
                        onPress={() => run('text', async () => onDrafts((await api.importText(slug, text)).drafts, 'your text (read by AI)'))}>
                  Read with AI
                </Button>
                <SlowNote active={busy === 'text'} />
              </>
            ) : (
              <Alert status="warning">
                <Alert.Indicator />
                <Alert.Content>
                  <Alert.Title>AI isn't configured</Alert.Title>
                  <Alert.Description>Set LLM_API_KEY on the server, or upload a spreadsheet instead.</Alert.Description>
                </Alert.Content>
              </Alert>
            )}
          </Tabs.Panel>

          <Tabs.Panel id="manual" className="pt-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Field label="Name" value={manual.name} onChange={setManualField('name')} isRequired />
              <Field label="Category" value={manual.category} onChange={setManualField('category')} />
              <Field label="Price ($)" value={manual.price} onChange={setManualField('price')} inputMode="decimal" />
              <Choice label="Pricing" value={manual.pricing} onChange={setManualField('pricing')}
                      options={Object.entries(meta.pricing).map(([id, label]) => ({ id, label }))} />
              <Field label="Unit" value={manual.unit} onChange={setManualField('unit')} placeholder="each" />
              <Field label="Tags" value={manual.tags} onChange={setManualField('tags')} placeholder="vegan, nut-free" />
              <Field label="Notice (hours)" value={manual.lead_time_hours} onChange={setManualField('lead_time_hours')}
                     inputMode="numeric" />
              <Field label="Description" value={manual.description} onChange={setManualField('description')} />
            </div>
            <Button className="mt-4" isDisabled={!manual.name.trim()} isPending={busy === 'manual'}
                    onPress={() => run('manual', async () => {
                      await api.addProduct(slug, manual)
                      setManual(emptyProduct())
                      await onAdded()
                      toast.success('Product added')
                    })}>
              Add product
            </Button>
          </Tabs.Panel>
        </Tabs>
        <ErrorAlert message={error} />
      </Card.Content>
    </Card>
  )
}

function ReviewProducts({ slug, meta, review, onChange, onDone }: {
  slug: string
  meta: Meta
  review: { rows: ReviewRow[]; source: string }
  onChange: (review: { rows: ReviewRow[]; source: string } | null) => void
  onDone: (saved: number) => Promise<void>
}) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pricingOptions = Object.entries(meta.pricing).map(([id, label]) => ({ id, label }))
  const kept = review.rows.filter((r) => r.keep)

  const update = (key: number, patch: Partial<ReviewRow>) =>
    onChange({ ...review, rows: review.rows.map((r) => (r.key === key ? { ...r, ...patch } : r)) })
  const setField = (row: ReviewRow, field: keyof ProductFields, value: string) =>
    update(row.key, { fields: { ...row.fields, [field]: value } })

  async function save() {
    setSaving(true)
    setError(null)
    try {
      const { saved } = await api.saveProducts(slug, kept.map((r) => r.fields))
      await onDone(saved)
    } catch (e) {
      // 422: the server returns every kept row with its errors, in the same order.
      const drafts = e instanceof ApiError ? (e.data as { drafts?: ProductDraft[] })?.drafts : undefined
      if (drafts) {
        const errorsByKey = new Map(kept.map((r, i) => [r.key, drafts[i]?.errors ?? []]))
        onChange({ ...review, rows: review.rows.map((r) => ({ ...r, errors: errorsByKey.get(r.key) ?? r.errors })) })
      }
      setError((e as Error).message)
      setSaving(false)
    }
  }

  const cell = (row: ReviewRow, field: keyof ProductFields, label: string, className = 'min-w-28') => (
    <TextField aria-label={`${label} for row ${row.key + 1}`} value={row.fields[field]}
               onChange={(v) => setField(row, field, v)} className={className}>
      <Input />
    </TextField>
  )

  return (
    <Card>
      <Card.Header>
        <Card.Title>Check {review.rows.length} products</Card.Title>
        <Card.Description>
          Read from {review.source}. Edit anything and untick rows you don't want. Nothing is saved until you click Save.
        </Card.Description>
      </Card.Header>
      <Card.Content className="flex flex-col gap-4">
        <Table>
          <Table.ScrollContainer>
            <Table.Content aria-label="Products to import" className="min-w-[1100px]">
              <Table.Header>
                <Table.Column>Keep</Table.Column>
                <Table.Column isRowHeader>Name</Table.Column>
                <Table.Column>Category</Table.Column>
                <Table.Column>Price ($)</Table.Column>
                <Table.Column>Pricing</Table.Column>
                <Table.Column>Unit</Table.Column>
                <Table.Column>Tags</Table.Column>
                <Table.Column>Notice (h)</Table.Column>
                <Table.Column>Description</Table.Column>
              </Table.Header>
              <Table.Body>
                {review.rows.map((row) => (
                  <Table.Row key={row.key} id={row.key} className={row.errors.length && row.keep ? 'bg-danger-soft' : ''}>
                    <Table.Cell>
                      <Checkbox aria-label={`Keep row ${row.key + 1}`} variant="secondary" isSelected={row.keep}
                                onChange={(keep) => update(row.key, { keep })}>
                        <Checkbox.Content><Checkbox.Control><Checkbox.Indicator /></Checkbox.Control></Checkbox.Content>
                      </Checkbox>
                    </Table.Cell>
                    <Table.Cell>
                      {cell(row, 'name', 'Name', 'min-w-44')}
                      {row.errors.length > 0 && <p className="mt-1 text-xs text-danger">{row.errors.join('; ')}</p>}
                    </Table.Cell>
                    <Table.Cell>{cell(row, 'category', 'Category')}</Table.Cell>
                    <Table.Cell>{cell(row, 'price', 'Price', 'w-24')}</Table.Cell>
                    <Table.Cell>
                      <div className="min-w-40">
                        <Choice ariaLabel={`Pricing for row ${row.key + 1}`} value={row.fields.pricing}
                                options={pricingOptions} onChange={(v) => setField(row, 'pricing', v)} />
                      </div>
                    </Table.Cell>
                    <Table.Cell>{cell(row, 'unit', 'Unit', 'w-20')}</Table.Cell>
                    <Table.Cell>{cell(row, 'tags', 'Tags')}</Table.Cell>
                    <Table.Cell>{cell(row, 'lead_time_hours', 'Notice', 'w-20')}</Table.Cell>
                    <Table.Cell>{cell(row, 'description', 'Description', 'min-w-44')}</Table.Cell>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Content>
          </Table.ScrollContainer>
        </Table>
        <ErrorAlert title="Not saved" message={error} />
      </Card.Content>
      <Card.Footer className="flex justify-between gap-3">
        <Button variant="secondary" onPress={() => onChange(null)}>Cancel</Button>
        <Button isDisabled={kept.length === 0} isPending={saving} onPress={save}>
          Save {kept.length} product{kept.length === 1 ? '' : 's'}
        </Button>
      </Card.Footer>
    </Card>
  )
}

function priceLabel(p: Product) {
  if (p.pricing === 'quote') return 'On request'
  return p.unit && p.unit !== 'each' ? `${money(p.price_cents)}/${p.unit}` : money(p.price_cents)
}

function ProductList({ slug, meta, products, onRemoved }: {
  slug: string
  meta: Meta
  products: Product[]
  onRemoved: () => Promise<void>
}) {
  const [removing, setRemoving] = useState<number | null>(null)

  async function remove(id: number) {
    setRemoving(id)
    try {
      await api.deleteProduct(slug, id)
      await onRemoved()
    } finally {
      setRemoving(null)
    }
  }

  return (
    <Table>
      <Table.ScrollContainer>
        <Table.Content aria-label="Products" className="min-w-[760px]">
          <Table.Header>
            <Table.Column isRowHeader>Name</Table.Column>
            <Table.Column>Category</Table.Column>
            <Table.Column>Price</Table.Column>
            <Table.Column>Pricing</Table.Column>
            <Table.Column>Tags</Table.Column>
            <Table.Column>Notice</Table.Column>
            <Table.Column><span className="sr-only">Actions</span></Table.Column>
          </Table.Header>
          <Table.Body>
            {products.map((p) => (
              <Table.Row key={p.id} id={p.id}>
                <Table.Cell>
                  <div className="font-medium">{p.name}</div>
                  {p.description && <div className="text-xs text-muted">{p.description}</div>}
                </Table.Cell>
                <Table.Cell>{p.category}</Table.Cell>
                <Table.Cell>{priceLabel(p)}</Table.Cell>
                <Table.Cell>{meta.pricing[p.pricing]}</Table.Cell>
                <Table.Cell>
                  <div className="flex flex-wrap gap-1">
                    {p.tags.split(',').filter(Boolean).map((t) => <Chip key={t} size="sm" variant="soft">{t}</Chip>)}
                  </div>
                </Table.Cell>
                <Table.Cell>{p.lead_time_hours ? `${p.lead_time_hours}h` : ''}</Table.Cell>
                <Table.Cell>
                  <Button size="sm" variant="ghost" isPending={removing === p.id} onPress={() => remove(p.id)}>
                    Remove
                  </Button>
                </Table.Cell>
              </Table.Row>
            ))}
          </Table.Body>
        </Table.Content>
      </Table.ScrollContainer>
    </Table>
  )
}
