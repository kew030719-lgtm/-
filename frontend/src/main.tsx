import { createRoot } from 'react-dom/client'

import App from './App'
import './styles/app.css'

const container = document.getElementById('root')
if (!container) throw new Error('missing #root')

// Deliberately not wrapped in StrictMode: it double-invokes effects in dev,
// which would fire the restore-and-poll bootstrap twice and duplicate task
// polling against the backend.
createRoot(container).render(<App />)