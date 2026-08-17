import { Link, Outlet, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../hooks/useAuth'
import { LanguageToggle } from '../components/LanguageToggle'

export function AppLayout() {
  const { t } = useTranslation()
  const { user, logout } = useAuth()
  const location = useLocation()
  const isProto = location.pathname.startsWith('/proto')

  return (
    <div className={`app ${isProto ? 'app-proto' : ''}`}>
      <header className="topbar">
        <Link to="/proto" className="topbar-brand">
          <span className="topbar-logo" aria-hidden="true">M</span>
          <span>
            <span className="topbar-title">MachineGKI</span>
            <span className="topbar-kicker">Operative Knowledge</span>
          </span>
        </Link>
        <div className="topbar-right">
          {user?.role === 'superadmin' && (
            <>
              <Link to="/fleet" className="topbar-nav-link">Fleet</Link>
              <Link to="/admin/users" className="topbar-nav-link">Users</Link>
            </>
          )}
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
      <main className={`main-content ${isProto ? 'main-content-proto' : ''}`}>
        <Outlet />
      </main>
    </div>
  )
}
