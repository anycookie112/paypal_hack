import { useEffect, useState } from 'react'
import { api } from './api'
import type { Meta } from './api'

let metaPromise: Promise<Meta> | null = null

/** App-wide information (name, whether AI is configured). Fetched once. */
export function useMeta(): Meta | null {
  const [meta, setMeta] = useState<Meta | null>(null)
  useEffect(() => {
    metaPromise ??= api.meta()
    metaPromise.then(setMeta).catch(() => { metaPromise = null })
  }, [])
  return meta
}
