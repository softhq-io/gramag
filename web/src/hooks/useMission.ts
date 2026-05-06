import { useState, useCallback } from 'react'
import { getBriefing, type Briefing } from '../api/mission'

export function useMission() {
  const [data, setData] = useState<Briefing | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchBriefing = useCallback(async (machineErpId: string, symptom = '') => {
    setIsLoading(true)
    setError(null)
    setData(null)
    try {
      const result = await getBriefing(machineErpId, symptom)
      if (result.error) {
        setError(result.error)
      } else {
        setData(result)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setIsLoading(false)
    }
  }, [])

  return { data, isLoading, error, fetchBriefing }
}
