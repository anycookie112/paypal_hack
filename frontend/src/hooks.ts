import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { BusinessDetail, Meta } from './api'

let metaPromise: Promise<Meta> | null = null

/** App-wide options (payment styles, pricing kinds, whether AI is configured). Fetched once. */
export function useMeta(): Meta | null {
  const [meta, setMeta] = useState<Meta | null>(null)
  useEffect(() => {
    metaPromise ??= api.meta()
    metaPromise.then(setMeta).catch(() => { metaPromise = null })
  }, [])
  return meta
}

/** A business with its products and rules; `reload` after any change. */
export function useBusiness(slug: string) {
  const [detail, setDetail] = useState<BusinessDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(async () => {
    try {
      setDetail(await api.business(slug))
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [slug])

  useEffect(() => { reload() }, [reload])
  return { detail, error, reload }
}
