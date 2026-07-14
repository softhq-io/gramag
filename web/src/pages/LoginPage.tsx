import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../hooks/useAuth'

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
      setError(e instanceof Error ? e.message : t('auth.loginError'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-form" onSubmit={handleSubmit}>
        <h1 className="login-title">{t('app.title')}</h1>
        <p className="login-subtitle">{t('app.subtitle')}</p>
        {error && <div className="login-error">{error}</div>}
        {passwordChangeToken ? (
          <>
            <p className="login-subtitle">{t('auth.changePasswordRequired')}</p>
            <input type="password" placeholder={t('auth.newPassword')} value={newPassword}
              onChange={e => setNewPassword(e.target.value)} autoFocus minLength={12} required />
            <input type="password" placeholder={t('auth.confirmPassword')} value={confirmPassword}
              onChange={e => setConfirmPassword(e.target.value)} minLength={12} required />
          </>
        ) : (
          <>
            <input type="email" placeholder={t('auth.email')} value={email}
              onChange={e => setEmail(e.target.value)} autoFocus required />
            <input type="password" placeholder={t('auth.password')} value={password}
              onChange={e => setPassword(e.target.value)} required />
          </>
        )}
        <button type="submit" disabled={loading}>
          {loading ? (
            <span className="btn-content">
              <span className="btn-spinner" />
              {t('auth.logging_in')}
            </span>
          ) : passwordChangeToken ? t('auth.setPassword') : t('auth.login')}
        </button>
        <div className="login-footer">
          <p className="login-powered">{t('app.poweredBy')}</p>
        </div>
      </form>
    </div>
  )
}
