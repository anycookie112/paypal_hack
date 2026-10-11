import { useTheme } from '@heroui/react'
import { Link, Route, Routes } from 'react-router'
import { useMeta } from './hooks'
import Home from './pages/Home'
import NewBusiness from './pages/NewBusiness'
import ProductsStep from './pages/ProductsStep'
import RulesStep from './pages/RulesStep'
import DoneStep from './pages/DoneStep'
import Storefront from './pages/Storefront'

export default function App() {
  useTheme('system') // follow the OS light/dark setting
  const meta = useMeta()

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex max-w-5xl items-center px-4 py-3">
          <Link to="/" className="font-semibold">{meta?.app_name ?? 'Order Assistant'}</Link>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 pt-8 pb-16">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/setup" element={<NewBusiness />} />
          <Route path="/b/:slug/setup/products" element={<ProductsStep />} />
          <Route path="/b/:slug/setup/rules" element={<RulesStep />} />
          <Route path="/b/:slug/setup/done" element={<DoneStep />} />
          <Route path="/b/:slug" element={<Storefront />} />
          <Route path="*" element={<p className="text-muted">Page not found. <Link to="/" className="link">Go home</Link></p>} />
        </Routes>
      </main>
    </div>
  )
}
