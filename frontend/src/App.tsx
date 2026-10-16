import { useTheme } from '@heroui/react'
import { Link, NavLink, Route, Routes } from 'react-router'
import { ROLE_LABEL } from './api'
import { Choice, ErrorAlert, Loading } from './components/ui'
import { useMember, useMeta } from './hooks'
import Claim from './pages/Claim'
import Home from './pages/Home'
import Items from './pages/Items'
import Review from './pages/Review'
import Settings from './pages/Settings'

const NAV = [
  { to: '/', label: 'Home' },
  { to: '/claim', label: 'Claim' },
  { to: '/review', label: 'Review', pics: true },
  { to: '/items', label: 'Items' },
  { to: '/settings', label: 'Settings' },
]

export default function App() {
  useTheme('system') // follow the OS light/dark setting
  const meta = useMeta()
  const { me, members, viewAs, error } = useMember()

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link to="/" className="font-semibold">{meta?.app_name ?? 'Group Fund'}</Link>
          <nav className="flex gap-4 text-sm">
            {NAV.filter((n) => !n.pics || me?.role !== 'member').map(({ to, label }) => (
              <NavLink key={to} to={to} end
                       className={({ isActive }) => isActive ? 'font-medium' : 'text-muted hover:text-foreground'}>
                {label}
              </NavLink>
            ))}
          </nav>
          {me && (
            <div className="ml-auto flex items-center gap-2 text-sm">
              <span className="text-muted">View as</span>
              <div className="w-48">
                <Choice ariaLabel="View as" value={String(me.id)} onChange={(id) => viewAs(Number(id))}
                        options={members.map((m) => ({ id: String(m.id), label: `${m.name} (${ROLE_LABEL[m.role]})` }))} />
              </div>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 pt-8 pb-16">
        <ErrorAlert title="Couldn't load the group" message={error} />
        {!me && !error && <Loading />}
        {me && (
          // Keyed by who's viewing: switching people reloads each page as them.
          <Routes key={me.id}>
            <Route path="/" element={<Home />} />
            <Route path="/claim" element={<Claim />} />
            <Route path="/review" element={<Review />} />
            <Route path="/items" element={<Items />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<p className="text-muted">Page not found. <Link to="/" className="link">Go home</Link></p>} />
          </Routes>
        )}
      </main>
    </div>
  )
}
