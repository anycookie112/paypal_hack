import { Alert, Button, Card, toast } from '@heroui/react'
import { useNavigate, useParams } from 'react-router'
import { SetupSteps } from '../components/SetupSteps'
import { ErrorAlert, Loading, PageTitle } from '../components/ui'
import { useBusiness, useMeta } from '../hooks'

export default function DoneStep() {
  const { slug = '' } = useParams()
  const navigate = useNavigate()
  const meta = useMeta()
  const { detail, error } = useBusiness(slug)

  if (error) return <ErrorAlert message={error} />
  if (!detail || !meta) return <Loading />

  const customerUrl = `${window.location.origin}/b/${slug}`

  return (
    <>
      <SetupSteps current="done" slug={slug} />
      <PageTitle title={`${detail.business.name} is set up`} />

      {detail.products.length === 0 && (
        <Alert status="warning" className="mb-4">
          <Alert.Indicator />
          <Alert.Content>
            <Alert.Title>No products yet</Alert.Title>
            <Alert.Description>Customers won't be able to order until you add some.</Alert.Description>
          </Alert.Content>
        </Alert>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <Card.Header><Card.Title>Summary</Card.Title></Card.Header>
          <Card.Content className="flex flex-col gap-4">
            <ul className="list-disc pl-5">
              <li>{detail.products.length} products</li>
              <li>{detail.rules.length} rules</li>
              <li>Payments go to {detail.business.paypal_email}</li>
            </ul>
            <div>
              <h3 className="mb-2 font-medium">How customers pay</h3>
              <ul className="flex flex-col gap-1">
                {detail.payment_by_category.map((p) => (
                  <li key={p.category || 'all'}>
                    <span className="font-medium">{p.category || 'All products'}:</span>{' '}
                    {meta.payment_styles[p.style]}
                    {p.style === 'deposit_balance' && ` (${p.deposit_percent}% deposit)`}
                    {p.style === 'hold_exact' && ` (+${p.buffer_percent}% hold)`}
                  </li>
                ))}
              </ul>
            </div>
          </Card.Content>
        </Card>

        <Card>
          <Card.Header>
            <Card.Title>Your customer link</Card.Title>
            <Card.Description>Share it on your website or social media, or print it as a QR code at the counter.</Card.Description>
          </Card.Header>
          <Card.Content className="flex flex-col items-start gap-3">
            <code className="rounded-lg border border-border px-3 py-2 text-sm break-all">{customerUrl}</code>
            <Button variant="secondary" size="sm" onPress={async () => {
              await navigator.clipboard.writeText(customerUrl)
              toast.success('Link copied')
            }}>
              Copy link
            </Button>
          </Card.Content>
        </Card>
      </div>

      <div className="mt-8 flex justify-between">
        <Button variant="secondary" onPress={() => navigate(`/b/${slug}/setup/rules`)}>Back</Button>
        <Button onPress={() => navigate(`/b/${slug}`)}>Open customer page</Button>
      </div>
    </>
  )
}
