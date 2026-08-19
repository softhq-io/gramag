const BASE = '/api'

export class ApiError extends Error {
  status: number
  code?: string
  retryAfter?: number

  constructor(message: string, status: number, code?: string, retryAfter?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.retryAfter = retryAfter
  }
}

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('access_token')
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string>) }
  if (!(opts.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(`${BASE}${path}`, { ...opts, headers })

  if (res.status === 401 && !path.startsWith('/auth/')) {
    // Try refresh
    const refreshed = await tryRefresh()
    if (refreshed) {
      headers['Authorization'] = `Bearer ${localStorage.getItem('access_token')}`
      const retry = await fetch(`${BASE}${path}`, { ...opts, headers })
      if (retry.ok) return parseResponse<T>(retry)
    }
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    window.location.href = '/einsatzplaner/login'
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = body.detail
    const retryAfterHeader = Number.parseInt(res.headers.get('Retry-After') || '', 10)
    const retryAfter = typeof detail?.retry_after === 'number'
      ? detail.retry_after
      : Number.isFinite(retryAfterHeader) ? retryAfterHeader : undefined
    const message = typeof detail === 'string' ? detail : detail?.message || `HTTP ${res.status}`
    throw new ApiError(message, res.status, detail?.code, retryAfter)
  }

  return parseResponse<T>(res)
}

function parseResponse<T>(response: Response): Promise<T> {
  if (response.status === 204 || response.headers.get('content-length') === '0') {
    return Promise.resolve(undefined as T)
  }
  return response.json() as Promise<T>
}

async function tryRefresh(): Promise<boolean> {
  const rt = localStorage.getItem('refresh_token')
  if (!rt) return false
  try {
    const res = await fetch(`${BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: rt }),
    })
    if (!res.ok) return false
    const data = await res.json()
    localStorage.setItem('access_token', data.access_token)
    return true
  } catch {
    return false
  }
}

export function get<T>(path: string) {
  return request<T>(path)
}

export function post<T>(path: string, body: unknown) {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body) })
}

export function patch<T>(path: string, body: unknown) {
  return request<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
}

export function del(path: string) {
  return request<void>(path, { method: 'DELETE' })
}

export function postForm<T>(path: string, body: FormData) {
  return request<T>(path, { method: 'POST', body })
}
