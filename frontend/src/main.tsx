import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { isWorkspaceSample } from './demo/workspaceFixture'

if (isWorkspaceSample()) {
  const { installWorkspaceSample } = await import('./demo/workspaceApi')
  installWorkspaceSample()
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
