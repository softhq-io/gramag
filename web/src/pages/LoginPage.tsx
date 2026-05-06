import { useState, useEffect, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../hooks/useAuth'

function formatNumber(n: number): string {
  return n.toLocaleString('de-CH')
}

export function LoginPage() {
  const { t } = useTranslation()
  const { login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [stats, setStats] = useState<{ nodes: number; relationships: number } | null>(null)

  useEffect(() => {
    fetch('/api/graph/stats')
      .then(r => r.json())
      .then(data => {
        const sumValues = (obj: Record<string, number> | number) =>
          typeof obj === 'number' ? obj : Object.values(obj).reduce((a, b) => a + b, 0)
        setStats({
          nodes: sumValues(data.nodes),
          relationships: sumValues(data.relationships),
        })
      })
      .catch(() => {})
  }, [])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
      navigate('/', { replace: true })
    } catch {
      setError(t('auth.loginError'))
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
        <input
          type="text"
          placeholder={t('auth.username')}
          value={username}
          onChange={e => setUsername(e.target.value)}
          autoFocus
          required
        />
        <input
          type="password"
          placeholder={t('auth.password')}
          value={password}
          onChange={e => setPassword(e.target.value)}
          required
        />
        <button type="submit" disabled={loading}>
          {loading ? (
            <span className="btn-content">
              <span className="btn-spinner" />
              {t('auth.logging_in')}
            </span>
          ) : t('auth.login')}
        </button>
        <div className="login-footer">
          <p className="login-powered">{t('app.poweredBy')}</p>
          {stats && (
            <div className="login-stats">
              <div className="login-stat">
                <div className="login-stat-value">{formatNumber(stats.nodes)}</div>
                <div className="login-stat-label">{t('search.nodes')}</div>
              </div>
              <div className="login-stat">
                <div className="login-stat-value">{formatNumber(stats.relationships)}</div>
                <div className="login-stat-label">{t('search.relationships')}</div>
              </div>
            </div>
          )}
        </div>
      </form>
    </div>
  )
}
