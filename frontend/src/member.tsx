import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { api, setMemberId } from './api'
import type { MemberInfo } from './api'
import { MemberContext } from './hooks'

const KEY = 'viewAs'

function remembered(): number | null {
  try {
    return Number(sessionStorage.getItem(KEY) ?? localStorage.getItem(KEY)) || null
  } catch {
    return null
  }
}

function remember(id: number) {
  try {
    sessionStorage.setItem(KEY, String(id))
    localStorage.setItem(KEY, String(id))
  } catch { /* private window: the choice lasts until reload */ }
}

/** Who's using the app: see MemberState in hooks.ts. */
export function MemberProvider({ children }: { children: ReactNode }) {
  const [members, setMembers] = useState<MemberInfo[]>([])
  const [me, setMe] = useState<MemberInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  const viewAs = (id: number) => {
    const member = members.find((m) => m.id === id)
    if (!member) return
    setMemberId(member.id)
    remember(member.id)
    setMe(member)
  }

  useEffect(() => {
    api.members().then((people) => {
      setMembers(people)
      const asName = new URLSearchParams(window.location.search).get('as')?.toLowerCase()
      const id = remembered()
      const chosen = people.find((m) => m.name.toLowerCase() === asName)
        ?? people.find((m) => m.id === id) ?? people[0] ?? null
      if (chosen) {
        setMemberId(chosen.id)
        remember(chosen.id)
      }
      setMe(chosen)
    }).catch((e: Error) => setError(e.message))
  }, [])

  return <MemberContext value={{ me, members, viewAs, error }}>{children}</MemberContext>
}
