import { Card } from '@heroui/react'
import { useParams } from 'react-router'
import { ErrorAlert, Loading, PageTitle } from '../components/ui'
import { useBusiness } from '../hooks'

/** The customer-facing page. Ordering with the assistant comes next. */
export default function Storefront() {
  const { slug = '' } = useParams()
  const { detail, error } = useBusiness(slug)

  if (error) return <ErrorAlert message={error} />
  if (!detail) return <Loading />

  return (
    <>
      <PageTitle title={detail.business.name} subtitle={detail.business.description} />
      <Card>
        <Card.Content className="text-muted">Ordering with the assistant is coming next.</Card.Content>
      </Card>
    </>
  )
}
