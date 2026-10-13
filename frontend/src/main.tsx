import { Toast } from '@heroui/react'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import App from './App.tsx'
import './index.css'
import { MemberProvider } from './member'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Toast.Provider placement="top end" />
      <MemberProvider>
        <App />
      </MemberProvider>
    </BrowserRouter>
  </StrictMode>,
)
