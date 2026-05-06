import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { getPartDetail } from '../api/mission'
import type { PartDetail } from '../api/mission'

export function PartDetailPage() {
  const { t } = useTranslation()
  const { partNummer } = useParams<{ partNummer: string }>()
  const [data, setData] = useState<PartDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!partNummer) return
    setLoading(true)
    setError('')
    getPartDetail(partNummer)
      .then(d => {
        if (d.error) setError(d.error)
        else setData(d)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [partNummer])

  if (loading) {
    return (
      <div className="briefing-loading">
        <div className="loading-spinner" />
        <div>{t('part.loading')}</div>
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="briefing-error">
        {error || t('part.notFound')}
      </div>
    )
  }

  return (
    <div className="part-detail-page">
      {/* Header */}
      <div className="card">
        <div className="part-detail-header">
          <span className="part-detail-nummer">{data.nummer}</span>
          <h2 className="part-detail-title">{data.titel || '—'}</h2>
        </div>
        <div className="part-detail-meta">
          {data.manufacturer_nr && (
            <div className="meta-row">
              <span className="meta-label">{t('part.manufacturerNr')}</span>
              <span className="mono">{data.manufacturer_nr}</span>
            </div>
          )}
          <div className="meta-row">
            <span className="meta-label">{t('part.usageCount')}</span>
            <span>{data.usage_count}</span>
          </div>
        </div>
      </div>

      {/* Usage History */}
      <div className="card">
        <h3 className="card-header">
          <span className="card-icon">&#128197;</span>
          {t('part.usageHistory')}
          <span className="card-count">{data.usage.length}</span>
        </h3>
        {data.usage.length === 0 ? (
          <div className="empty-state">{t('part.usageEmpty')}</div>
        ) : (
          <div className="part-usage-table">
            {data.usage.map((u, i) => (
              <div key={i} className="part-usage-row">
                <span className="part-usage-date">{u.date || '—'}</span>
                <span className="part-usage-machine">
                  {u.machine_erp_id ? (
                    <Link to={`/mission/${u.machine_erp_id}`} className="part-link">
                      {u.machine || '—'}
                    </Link>
                  ) : (
                    u.machine || '—'
                  )}
                </span>
                <span className="part-usage-customer">{u.customer || '—'}</span>
                <span className="part-usage-job">{u.job || '—'}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Co-occurrence Parts */}
      {data.co_parts.filter(cp => cp.nummer).length > 0 && (
        <div className="card">
          <h3 className="card-header">
            <span className="card-icon">&#128279;</span>
            {t('part.coParts')}
            <span className="card-count">{data.co_parts.filter(cp => cp.nummer).length}</span>
          </h3>
          <div className="part-co-list">
            {data.co_parts.filter(cp => cp.nummer).map((cp, i) => (
              <div key={i} className="part-co-row">
                <Link to={`/part/${cp.nummer}`} className="part-link mono">
                  {cp.nummer}
                </Link>
                <span className="part-co-titel">{cp.titel}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Manual References */}
      {data.manual_refs.length > 0 && (
        <div className="card">
          <h3 className="card-header">
            <span className="card-icon">&#128214;</span>
            {t('part.manualRefs')}
            <span className="card-count">{data.manual_refs.length}</span>
          </h3>
          <div className="part-manual-list">
            {data.manual_refs.map((title, i) => (
              <div key={i} className="part-manual-item">{title}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
