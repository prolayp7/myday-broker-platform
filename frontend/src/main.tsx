import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { installAuthInterceptor } from './auth/authInterceptor'
import { initTheme } from './lib/theme'

// Installed once, outside React — every admin request goes through this
// from the very first render, including calls made before any component
// with access to AuthContext has mounted.
installAuthInterceptor()

// Same reason, same place: applies the saved theme and follows the OS while
// the app is open. index.html already set the class for the first paint; this
// keeps it in step from here on.
initTheme()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
