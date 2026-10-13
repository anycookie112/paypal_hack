import { useTheme } from '@heroui/react'
import { Link, Route, Routes } from 'react-router'
import { useMeta } from './hooks'
import Home from './pages/Home'

export default function App() {
  useTheme('system') // follow the OS light/dark setting
  const meta = useMeta()

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex max-w-5xl items-center px-4 py-3">
          <Link to="/" className="font-semibold">{meta?.app_name ?? 'Group Fund'}</Link>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 pt-8 pb-16">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="*" element={<p className="text-muted">Page not found. <Link to="/" className="link">Go home</Link></p>} />
        </Routes>
      </main>
    </div>
  )
}
