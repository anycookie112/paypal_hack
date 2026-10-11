import { Button, Card, Chip } from '@heroui/react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { api } from '../api'
import type { Business } from '../api'
import { ErrorAlert, Loading } from '../components/ui'

export default function Home() {
  const navigate = useNavigate()
  const [businesses, setBusinesses] = useState<Business[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.businesses().then(setBusinesses).catch((e: Error) => setError(e.message))
  }, [])

  return (
    <div className="flex flex-col gap-10">
      <section className="flex flex-col items-start gap-4 py-6">
        <h1 className="max-w-2xl text-3xl font-semibold tracking-tight sm:text-4xl">
          An AI ordering assistant for your small business
        </h1>
        <p className="max-w-2xl text-lg text-muted">
          Add your products and rules once. Customers describe what they need and their budget, and the assistant
          builds an order that follows your rules, with PayPal handling deposits, holds and exact final charges.
        </p>
        <Button size="lg" onPress={() => navigate('/setup')}>Set up a business</Button>
      </section>

      <section>
        <h2 className="mb-3 text-lg font-semibold">Businesses</h2>
        <ErrorAlert message={error} />
        {!businesses && !error && <Loading />}
        {businesses?.length === 0 && (
          <p className="text-muted">No businesses yet. Set up your first one to get started.</p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          {businesses?.map((b) => (
            <Card key={b.slug}>
              <Card.Header>
                <Card.Title className="flex items-center gap-2">
                  {b.name} {b.kind && <Chip size="sm">{b.kind}</Chip>}
                </Card.Title>
                {b.description && <Card.Description>{b.description}</Card.Description>}
              </Card.Header>
              <Card.Footer className="flex gap-4">
                <Link className="link" to={`/b/${b.slug}/setup/products`}>Manage</Link>
                <Link className="link" to={`/b/${b.slug}`}>Customer page</Link>
              </Card.Footer>
            </Card>
          ))}
        </div>
      </section>
    </div>
  )
}
