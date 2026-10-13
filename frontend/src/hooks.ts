import { createContext, use, useEffect, useState } from 'react'
import { api } from './api'
import type { MemberInfo, Meta } from './api'

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

/** "View as" (docs/SPEC.md §13): no passwords, pick who you are. `?as=sam` opens a tab as Sam, so two tabs can be
 *  two people: each tab remembers its own choice (sessionStorage), and a new tab starts as the last one picked. */
interface MemberState {
  me: MemberInfo | null
  members: MemberInfo[]
  viewAs: (id: number) => void
  error: string | null
}

export const MemberContext = createContext<MemberState>({ me: null, members: [], viewAs: () => {}, error: null })

/** Who's using the app, everyone they can switch to, and how to switch (MemberProvider in member.tsx). */
export const useMember = () => use(MemberContext)
