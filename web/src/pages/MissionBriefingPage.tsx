import { useEffect, useState, useRef } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMission } from '../hooks/useMission'
import { MachineInfoCard } from '../components/MachineInfoCard'
import { BriefingSummary } from '../components/BriefingSummary'
import { ServiceHistoryCard } from '../components/ServiceHistoryCard'
import { PartsKitCard } from '../components/PartsKitCard'
import { ManualRefsCard } from '../components/ManualRefsCard'
import { PredictedNeedsCard } from '../components/PredictedNeedsCard'
import { ReasoningPath } from '../components/ReasoningPath'

const LOADING_STEP_KEYS = ['machine', 'history', 'parts', 'manuals', 'ai'] as const

function LoadingSteps({ t }: { t: (key: string) => string }) {
  const [activeIdx, setActiveIdx] = useState(0)
  const timerRef = useRef<ReturnType<typeof setInterval>>(undefined)

  useEffect(() => {
    timerRef.current = setInterval(() => {
      setActiveIdx(prev => (prev < LOADING_STEP_KEYS.length - 1 ? prev + 1 : prev))
    }, 1800)
    return () => clearInterval(timerRef.current)
  }, [])

  return (
    <div className="loading-steps">
      {LOADING_STEP_KEYS.map((key, i) => {
        const isDone = i < activeIdx
        const isActive = i === activeIdx
        return (
          <div
            key={key}
            className={`loading-step ${isDone ? 'done' : isActive ? 'active' : 'pending'}`}
            style={{ animationDelay: `${i * 200}ms` }}
          >
            <span className="loading-step-check">
              {isDone ? '\u2713' : isActive ? <span className="loading-spinner-sm" /> : '\u25CB'}
            </span>
            {t(`briefing.loadingSteps.${key}`)}
          </div>
        )
      })}
    </div>
  )
}

export function MissionBriefingPage() {
  const { t } = useTranslation()
  const { machineErpId } = useParams<{ machineErpId: string }>()
  const [searchParams] = useSearchParams()
  const symptom = searchParams.get('symptom') || ''
  const { data, isLoading, error, fetchBriefing } = useMission()

  // Mobile accordion state — only affects visibility on narrow screens
  const [openSections, setOpenSections] = useState<Set<string>>(
    new Set(['machine', 'summary'])
  )

  const toggleSection = (section: string) => {
    setOpenSections(prev => {
      const next = new Set(prev)
      if (next.has(section)) next.delete(section)
      else next.add(section)
      return next
    })
  }

  useEffect(() => {
    if (machineErpId) {
      fetchBriefing(machineErpId, symptom)
    }
  }, [machineErpId, symptom, fetchBriefing])

  if (isLoading) {
    return (
      <div className="briefing-loading">
        <div className="loading-spinner" />
        <p>{t('briefing.loading')}</p>
        <LoadingSteps t={t} />
      </div>
    )
  }

  if (error) {
    return (
      <div className="briefing-error">
        <p>{error}</p>
      </div>
    )
  }

  if (!data) return null

  return (
    <div className="briefing-page">
      <ReasoningPath steps={data.reasoning_path} />

      <div className="briefing-grid">
        <div className="briefing-main">
          <Section id="machine" label={t('briefing.machine')} open={openSections} toggle={toggleSection}>
            <MachineInfoCard machine={data.machine} />
          </Section>

          <Section id="summary" label={t('briefing.summary')} open={openSections} toggle={toggleSection}>
            <BriefingSummary summary={data.summary} />
          </Section>

          <Section id="history" label={t('briefing.history')} open={openSections} toggle={toggleSection}>
            <ServiceHistoryCard history={data.history} />
          </Section>
        </div>

        <div className="briefing-sidebar">
          <Section id="parts" label={t('briefing.partsKit')} open={openSections} toggle={toggleSection}>
            <PartsKitCard kit={data.parts_kit} />
          </Section>

          <Section id="manuals" label={t('briefing.manuals')} open={openSections} toggle={toggleSection}>
            <ManualRefsCard manuals={data.manuals} />
          </Section>

          <Section id="predicted" label={t('predicted.title')} open={openSections} toggle={toggleSection}>
            <PredictedNeedsCard erpId={machineErpId!} />
          </Section>
        </div>
      </div>
    </div>
  )
}

function Section({ id, label, open, toggle, children }: {
  id: string
  label: string
  open: Set<string>
  toggle: (id: string) => void
  children: React.ReactNode
}) {
  return (
    <div className={`accordion-section ${open.has(id) ? 'is-open' : ''}`}>
      <button className="accordion-toggle" onClick={() => toggle(id)}>
        {label}
      </button>
      <div className="accordion-body">
        {children}
      </div>
    </div>
  )
}
