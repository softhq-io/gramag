import { Outlet } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../hooks/useAuth'
import { LanguageToggle } from '../components/LanguageToggle'

export function AppLayout() {
  const { t } = useTranslation()
  const { user, logout } = useAuth()

  return (
    <div className="app">
      <header className="topbar">
        <a href="/einsatzplaner/" className="topbar-brand">
          <span className="topbar-title">{t('app.title')}</span>
          <span className="topbar-badge">
            {import.meta.env.VITE_PROTO_ONLY === '1' ? 'Wissensdatenbank' : 'Mission Control'}
          </span>
        </a>
        <nav className="topbar-nav">
          {import.meta.env.VITE_PROTO_ONLY === '1' ? (
            <a href="/einsatzplaner/proto" className="topbar-nav-link">Wissensdatenbank</a>
          ) : (
            <>
              <a href="/einsatzplaner/" className="topbar-nav-link">{t('search.machines')}</a>
              <a href="/einsatzplaner/fleet" className="topbar-nav-link">{t('fleet.title')}</a>
              <a href="/einsatzplaner/proto" className="topbar-nav-link">Proto KB</a>
            </>
          )}
        </nav>
        <div className="topbar-right">
          <LanguageToggle />
          {user && (
            <>
              <span className="topbar-user">{user.name}</span>
              <button className="topbar-logout" onClick={logout}>
                {t('auth.logout')}
              </button>
            </>
          )}
        </div>
      </header>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  )
}
