import type { ReasoningStep } from '../api/mission'

export function ReasoningPath({ steps }: { steps: ReasoningStep[] }) {
  if (!steps.length) return null

  return (
    <div className="reasoning-path">
      {steps.map((s, i) => (
        <div
          key={i}
          className="reasoning-step"
          style={{ animationDelay: `${i * 150}ms` }}
        >
          <div className={`reasoning-dot ${i === steps.length - 1 ? 'reasoning-dot-done' : ''}`}>
            {i === steps.length - 1 ? '\u2713' : i + 1}
          </div>
          <div className="reasoning-label">{s.step}</div>
          <div className="reasoning-detail">{s.detail}</div>
          {i < steps.length - 1 && <div className="reasoning-arrow">{'\u2192'}</div>}
        </div>
      ))}
    </div>
  )
}
