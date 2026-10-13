import { Button, Card, Chip, Modal, Switch, Table, toast } from '@heroui/react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { api, dollars, money, toCents } from '../api'
import type { Catalog, CatalogCategory, CatalogItem, Proof } from '../api'
import { Choice, ErrorAlert, Field, Loading, PageTitle } from '../components/ui'
import { useMember } from '../hooks'

const PROOF_LABEL: Record<Proof, string> = { receipt: 'Receipt', receipt_photo: 'Receipt + photo' }

type Editing =
  | { kind: 'item'; item: CatalogItem | null; categoryId: number }
  | { kind: 'category'; category: CatalogCategory | null }

/** The common items: pre-approved up to their limit. Everyone sees the list; the treasurer changes it. */
export default function Items() {
  const { me } = useMember()
  const canEdit = me?.role === 'admin'
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<Editing | null>(null)

  useEffect(() => {
    api.catalog().then(setCatalog).catch((e: Error) => setError(e.message))
  }, [])

  const switchItem = async (item: CatalogItem, active: boolean) => {
    try {
      setCatalog(await api.updateItem(item.id, { active }))
      toast.success(`${item.name} ${active ? 'is back on the list' : 'is switched off'}.`)
    } catch (e) {
      toast.danger((e as Error).message)
    }
  }

  if (error) return <ErrorAlert title="Couldn't load the items" message={error} />
  if (!catalog) return <Loading />

  return (
    <>
      <PageTitle title="Common items" subtitle={
        <>Pre-approved up to their limit: say you're getting one and it's confirmed straight away. Anything else,
          or more than the limit, goes to a PIC.</>
      } />
      <div className="mb-6 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span>Group budget: <b>{catalog.monthly_budget_cents ? `${money(catalog.monthly_budget_cents)} a month` : 'none'}</b></span>
        <span>Most any one purchase: <b>{money(catalog.hard_ceiling_cents)}</b></span>
        <Link to="/settings" className="link">Settings</Link>
        {canEdit
          ? <Button size="sm" variant="secondary" className="ml-auto"
                    onPress={() => setEditing({ kind: 'category', category: null })}>Add category</Button>
          : <span className="ml-auto text-muted">Only the treasurer can change this list.</span>}
      </div>

      <div className="flex flex-col gap-6">
        {catalog.categories.map((category) => (
          <Card key={category.id}>
            <Card.Header className="flex flex-row flex-wrap items-center gap-x-4 gap-y-2">
              <div>
                <Card.Title>{category.name}</Card.Title>
                <Card.Description>
                  {category.monthly_budget_cents ? `Budget ${money(category.monthly_budget_cents)} a month` : 'No budget of its own'}
                </Card.Description>
              </div>
              {canEdit && (
                <div className="ml-auto flex gap-2">
                  <Button size="sm" variant="ghost" onPress={() => setEditing({ kind: 'category', category })}>Edit</Button>
                  <Button size="sm" variant="secondary"
                          onPress={() => setEditing({ kind: 'item', item: null, categoryId: category.id })}>Add item</Button>
                </div>
              )}
            </Card.Header>
            <Card.Content>
              <ItemTable category={category} canEdit={canEdit} onSwitch={switchItem}
                         onEdit={(item) => setEditing({ kind: 'item', item, categoryId: category.id })} />
            </Card.Content>
          </Card>
        ))}
      </div>

      {editing?.kind === 'item' && (
        <ItemForm item={editing.item} categoryId={editing.categoryId} catalog={catalog}
                  onSaved={setCatalog} onClose={() => setEditing(null)} />
      )}
      {editing?.kind === 'category' && (
        <CategoryForm category={editing.category} onSaved={setCatalog} onClose={() => setEditing(null)} />
      )}
    </>
  )
}

function ItemTable({ category, canEdit, onSwitch, onEdit }: {
  category: CatalogCategory
  canEdit: boolean
  onSwitch: (item: CatalogItem, active: boolean) => void
  onEdit: (item: CatalogItem) => void
}) {
  return (
    <Table variant="secondary">
      <Table.ScrollContainer>
        <Table.Content aria-label={`${category.name} items`} className="table-fixed min-w-[640px]">
          <Table.Header>
            <Table.Column isRowHeader className="w-[36%]">Item</Table.Column>
            <Table.Column className="w-[12%]">Limit</Table.Column>
            <Table.Column className="w-[14%]">Lasts about</Table.Column>
            <Table.Column className="w-[16%]">Proof</Table.Column>
            <Table.Column className="w-[12%]">On the list</Table.Column>
            <Table.Column>{canEdit ? <span className="sr-only">Edit</span> : null}</Table.Column>
          </Table.Header>
          <Table.Body renderEmptyState={() => <p className="py-4 text-center text-muted">No items yet.</p>}>
            {category.items.map((item) => (
              <Table.Row key={item.id} id={item.id} className={item.active ? '' : 'opacity-60'}>
                <Table.Cell>
                  <div className="font-medium">{item.name}</div>
                  {item.aliases.length > 0 && <div className="text-xs text-muted">{item.aliases.join(', ')}</div>}
                </Table.Cell>
                <Table.Cell>{money(item.cap_cents)}</Table.Cell>
                <Table.Cell>{item.restock_days} {item.restock_days === 1 ? 'day' : 'days'}</Table.Cell>
                <Table.Cell>{PROOF_LABEL[item.proof]}</Table.Cell>
                <Table.Cell>
                  {canEdit ? (
                    <Switch size="sm" isSelected={item.active} onChange={(on) => onSwitch(item, on)}
                            aria-label={`${item.name} on the list`}>
                      <Switch.Control><Switch.Thumb /></Switch.Control>
                    </Switch>
                  ) : (
                    <Chip size="sm" variant="soft" color={item.active ? 'success' : 'default'}>{item.active ? 'On' : 'Off'}</Chip>
                  )}
                </Table.Cell>
                <Table.Cell>
                  {canEdit && <Button size="sm" variant="ghost" onPress={() => onEdit(item)}>Edit</Button>}
                </Table.Cell>
              </Table.Row>
            ))}
          </Table.Body>
        </Table.Content>
      </Table.ScrollContainer>
    </Table>
  )
}

/** A dialog with a form: Save calls `save`, which returns an error message or null when done. */
function FormDialog({ title, onClose, save, children }: {
  title: string
  onClose: () => void
  save: () => Promise<string | null>
  children: React.ReactNode
}) {
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    const problem = await save()
    setSaving(false)
    if (problem) setError(problem)
    else onClose()
  }

  return (
    <Modal isOpen onOpenChange={(open) => !open && onClose()}>
      <Modal.Backdrop>
        <Modal.Container>
          <Modal.Dialog>
            <form onSubmit={submit}>
              <Modal.Header><Modal.Heading>{title}</Modal.Heading></Modal.Header>
              <Modal.Body className="flex flex-col gap-4">
                {children}
                <ErrorAlert title="Not saved" message={error} />
              </Modal.Body>
              <Modal.Footer>
                <Button variant="ghost" onPress={onClose}>Cancel</Button>
                <Button type="submit" isDisabled={saving}>{saving ? 'Saving…' : 'Save'}</Button>
              </Modal.Footer>
            </form>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </Modal>
  )
}

function ItemForm({ item, categoryId, catalog, onSaved, onClose }: {
  item: CatalogItem | null
  categoryId: number
  catalog: Catalog
  onSaved: (catalog: Catalog) => void
  onClose: () => void
}) {
  const [name, setName] = useState(item?.name ?? '')
  const [aliases, setAliases] = useState(item?.aliases.join(', ') ?? '')
  const [category, setCategory] = useState(String(categoryId))
  const [cap, setCap] = useState(item ? dollars(item.cap_cents) : '')
  const [days, setDays] = useState(String(item?.restock_days ?? 14))
  const [proof, setProof] = useState<Proof>(item?.proof ?? 'receipt')

  const save = async () => {
    const capCents = toCents(cap)
    if (capCents === null) return 'Enter the limit as an amount, like 25 or 25.50.'
    const restockDays = Number(days)
    if (!Number.isInteger(restockDays)) return 'Enter how long it lasts as a whole number of days.'
    const fields = {
      category_id: Number(category), name, cap_cents: capCents, restock_days: restockDays, proof,
      aliases: aliases.split(',').map((a) => a.trim()).filter(Boolean),
    }
    try {
      onSaved(item ? await api.updateItem(item.id, fields) : await api.addItem(fields))
      toast.success(`${name.trim()} saved.`)
      return null
    } catch (e) {
      return (e as Error).message
    }
  }

  return (
    <FormDialog title={item ? `Edit ${item.name}` : 'Add an item'} onClose={onClose} save={save}>
      <Field label="Name" value={name} onChange={setName} isRequired placeholder="Gloves" />
      <Field label="Other names" value={aliases} onChange={setAliases} placeholder="work gloves, cleanup gloves"
             description="Separated by commas: what people might call it, so the AI recognises it." />
      <Choice label="Category" value={category} onChange={setCategory}
              options={catalog.categories.map((c) => ({ id: String(c.id), label: c.name }))} />
      <div className="grid grid-cols-2 gap-4">
        <Field label="Limit per purchase ($)" value={cap} onChange={setCap} inputMode="decimal" isRequired
               description={`Up to ${money(catalog.hard_ceiling_cents)}. Applies to new requests.`} />
        <Field label="Lasts about (days)" value={days} onChange={setDays} inputMode="numeric" isRequired
               description="Bought again much sooner: a PIC decides." />
      </div>
      <Choice label="Proof" value={proof} onChange={(p) => setProof(p as Proof)}
              options={Object.entries(PROOF_LABEL).map(([id, label]) => ({ id, label }))} />
    </FormDialog>
  )
}

function CategoryForm({ category, onSaved, onClose }: {
  category: CatalogCategory | null
  onSaved: (catalog: Catalog) => void
  onClose: () => void
}) {
  const [name, setName] = useState(category?.name ?? '')
  const [budget, setBudget] = useState(category?.monthly_budget_cents ? dollars(category.monthly_budget_cents) : '')

  const save = async () => {
    const budgetCents = budget.trim() ? toCents(budget) : 0
    if (budgetCents === null) return 'Enter the budget as an amount, like 120, or leave it empty for none.'
    const fields = { name, monthly_budget_cents: budgetCents }
    try {
      onSaved(category ? await api.updateCategory(category.id, fields) : await api.addCategory(fields))
      toast.success(`${name.trim()} saved.`)
      return null
    } catch (e) {
      return (e as Error).message
    }
  }

  return (
    <FormDialog title={category ? `Edit ${category.name}` : 'Add a category'} onClose={onClose} save={save}>
      <Field label="Name" value={name} onChange={setName} isRequired placeholder="Supplies" />
      <Field label="Monthly budget ($)" value={budget} onChange={setBudget} inputMode="decimal"
             description="Leave empty for no budget of its own: the group's budget still applies." />
    </FormDialog>
  )
}
