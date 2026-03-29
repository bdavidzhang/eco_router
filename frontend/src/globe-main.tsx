import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { SCIGlobeDashboard } from './components/globe/SCIGlobeDashboard'

const container = document.getElementById('globe-root')!
createRoot(container).render(
  <StrictMode>
    <SCIGlobeDashboard />
  </StrictMode>
)
