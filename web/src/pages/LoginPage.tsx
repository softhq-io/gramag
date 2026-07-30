import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../hooks/useAuth'
import { ApiError } from '../api/client'
import { LanguageToggle } from '../components/LanguageToggle'

export function LoginPage() {
  const { t } = useTranslation()
  const { login, changeInitialPassword } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [passwordChangeToken, setPasswordChangeToken] = useState<string | null>(null)
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [cooldownSeconds, setCooldownSeconds] = useState(0)

  useEffect(() => {
    if (cooldownSeconds <= 0) return
    const timer = window.setTimeout(() => {
      setCooldownSeconds(current => Math.max(0, current - 1))
    }, 1000)
    return () => window.clearTimeout(timer)
  }, [cooldownSeconds])

  const cooldownMinutes = Math.max(1, Math.ceil(cooldownSeconds / 60))

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      if (passwordChangeToken) {
        if (newPassword !== confirmPassword) {
          setError(t('auth.passwordMismatch'))
          return
        }
        await changeInitialPassword(passwordChangeToken, newPassword)
        navigate('/', { replace: true })
      } else {
        const result = await login(email, password)
        if (result.passwordChangeToken) {
          setPasswordChangeToken(result.passwordChangeToken)
          setPassword('')
        } else {
          navigate('/', { replace: true })
        }
      }
    } catch (e) {
      if (e instanceof ApiError && e.code === 'login_cooldown') {
        setCooldownSeconds(e.retryAfter || 15 * 60)
        setError('')
      } else if (e instanceof ApiError && e.status === 401) {
        setError(t('auth.loginError'))
      } else {
        setError(e instanceof Error ? e.message : t('auth.loginError'))
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-language">
        <LanguageToggle />
      </div>
      <main className="login-shell">
        <div className="login-brand" aria-label="MachineGKI">
          <span className="login-brand-mark" aria-hidden="true">M</span>
          <span>
            <strong>MachineGKI</strong>
            <small>{t('app.operationalKnowledge')}</small>
          </span>
        </div>

        <header className="login-intro">
          <span className="login-eyebrow">
            <span className="login-status-dot" aria-hidden="true" />
            {t('auth.secureAccess')}
          </span>
          <h1>{passwordChangeToken ? t('auth.setPassword') : t('auth.welcome')}</h1>
          <p>
            {passwordChangeToken ? t('auth.changePasswordRequired') : t('auth.loginHint')}
          </p>
        </header>

        <form className="login-form" onSubmit={handleSubmit} aria-label={t('auth.login')}>
          {error && <div className="login-error">{error}</div>}
          {cooldownSeconds > 0 && (
            <div className="login-error">
              {t('auth.loginCooldown', { count: cooldownMinutes })}
            </div>
          )}
          <div className="login-fields">
            {passwordChangeToken ? (
              <>
                <label className="login-field">
                  <span>{t('auth.newPassword')}</span>
                  <span className="login-control">
                    <LockIcon />
                    <input type="password" placeholder={t('auth.newPassword')} value={newPassword}
                      onChange={e => setNewPassword(e.target.value)} autoFocus minLength={12} required />
                  </span>
                </label>
                <label className="login-field">
                  <span>{t('auth.confirmPassword')}</span>
                  <span className="login-control">
                    <LockIcon />
                    <input type="password" placeholder={t('auth.confirmPassword')} value={confirmPassword}
                      onChange={e => setConfirmPassword(e.target.value)} minLength={12} required />
                  </span>
                </label>
              </>
            ) : (
              <>
                <label className="login-field">
                  <span>{t('auth.username')}</span>
                  <span className="login-control">
                    <UserIcon />
                    <input type="text" name="username" autoComplete="username"
                      placeholder={t('auth.username')} value={email}
                      onChange={e => {
                        setEmail(e.target.value)
                        setCooldownSeconds(0)
                        setError('')
                      }} autoFocus required />
                  </span>
                </label>
                <label className="login-field">
                  <span>{t('auth.password')}</span>
                  <span className="login-control">
                    <LockIcon />
                    <input type="password" name="password" autoComplete="current-password"
                      placeholder={t('auth.password')} value={password}
                      onChange={e => setPassword(e.target.value)} required />
                  </span>
                </label>
              </>
            )}
          </div>
          <button className="login-submit" type="submit" disabled={loading || cooldownSeconds > 0}>
            {loading ? (
              <span className="btn-content">
                <span className="btn-spinner" />
                {t('auth.logging_in')}
              </span>
            ) : (
              <span className="btn-content">
                {passwordChangeToken ? t('auth.setPassword') : t('auth.login')}
                <ArrowIcon />
              </span>
            )}
          </button>
          <footer className="login-footer">
            <p className="login-powered">{t('app.poweredBy')}</p>
          </footer>
        </form>
      </main>
    </div>
  )
}

function UserIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>
}

function LockIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></svg>
}

function ArrowIcon() {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
}
