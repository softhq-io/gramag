import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  createUser,
  listClients,
  listUsers,
  resetUserPassword,
  updateUser,
  type AdminClient,
  type AdminUser,
} from '../api/admin'
import type { UserRole } from '../api/auth'

const ROLES: UserRole[] = ['user', 'all_clients', 'superadmin']

export function UsersAdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [clients, setClients] = useState<AdminClient[]>([])
  const [error, setError] = useState('')
  const [temporaryPassword, setTemporaryPassword] = useState('')
  const [identifier, setIdentifier] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState<UserRole>('user')
  const [clientIds, setClientIds] = useState<string[]>([])
  const [busy, setBusy] = useState(false)

  const clientsById = useMemo(
    () => new Map(clients.map(client => [client.id, client])),
    [clients],
  )

  async function reload() {
    const [nextUsers, nextClients] = await Promise.all([listUsers(), listClients()])
    setUsers(nextUsers)
    setClients(nextClients)
  }

  useEffect(() => {
    reload().catch(e => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const result = await createUser({
        ...(identifier.includes('@')
          ? { email: identifier.trim() }
          : { username: identifier.trim() }),
        name,
        role,
        client_ids: role === 'user' ? clientIds : [],
      })
      setTemporaryPassword(result.temporary_password)
      setIdentifier('')
      setName('')
      setRole('user')
      setClientIds([])
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function save(user: AdminUser) {
    setError('')
    try {
      await updateUser(user.id, {
        name: user.name,
        role: user.role,
        active: user.active,
        client_ids: user.role === 'user' ? user.client_ids : [],
      })
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  async function reset(user: AdminUser) {
    if (!window.confirm(`Reset password for ${user.identifier}?`)) return
    try {
      const result = await resetUserPassword(user.id)
      setTemporaryPassword(result.temporary_password)
      await reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  function edit(id: string, changes: Partial<AdminUser>) {
    setUsers(current => current.map(user => user.id === id ? { ...user, ...changes } : user))
  }

  return (
    <div className="admin-users-page">
      <div className="admin-users-header">
        <div>
          <h1>User administration</h1>
          <p>Create accounts, assign clients, and revoke access immediately.</p>
        </div>
      </div>

      {error && <div className="admin-message error">{error}</div>}
      {temporaryPassword && (
        <div className="admin-message password">
          <strong>Temporary password — shown once:</strong>
          <code>{temporaryPassword}</code>
          <button onClick={() => navigator.clipboard.writeText(temporaryPassword)}>Copy</button>
          <button onClick={() => setTemporaryPassword('')}>Dismiss</button>
        </div>
      )}

      <form className="admin-user-create" onSubmit={submit}>
        <h2>Add user</h2>
        <input type="text" autoComplete="off" placeholder="Email or username"
          value={identifier} onChange={e => setIdentifier(e.target.value)} required />
        <input placeholder="Display name" value={name} onChange={e => setName(e.target.value)} required />
        <select value={role} onChange={e => setRole(e.target.value as UserRole)}>
          {ROLES.map(value => <option key={value} value={value}>{roleLabel(value)}</option>)}
        </select>
        {role === 'user' && (
          <ClientSelect clients={clients} selected={clientIds} onChange={setClientIds} />
        )}
        <button type="submit" disabled={busy}>{busy ? 'Creating…' : 'Create user'}</button>
      </form>

      <div className="admin-user-list">
        <h2>Users</h2>
        {users.map(user => (
          <div className={`admin-user-row ${user.active ? '' : 'inactive'}`} key={user.id}>
            <div className="admin-user-identity">
              <input value={user.name} onChange={e => edit(user.id, { name: e.target.value })} />
              <span>{user.identifier}</span>
              {user.must_change_password && <small>Password change required</small>}
            </div>
            <select value={user.role} onChange={e => edit(user.id, { role: e.target.value as UserRole })}>
              {ROLES.map(value => <option key={value} value={value}>{roleLabel(value)}</option>)}
            </select>
            <label className="admin-active">
              <input type="checkbox" checked={user.active}
                onChange={e => edit(user.id, { active: e.target.checked })} /> Active
            </label>
            <div className="admin-client-cell">
              {user.role === 'user' ? (
                <ClientSelect clients={clients} selected={user.client_ids}
                  onChange={ids => edit(user.id, { client_ids: ids })} />
              ) : (
                <span>All clients</span>
              )}
            </div>
            <div className="admin-user-actions">
              <button onClick={() => save(user)}>Save</button>
              <button className="secondary" onClick={() => reset(user)}>Reset password</button>
            </div>
            <div className="admin-user-grants">
              {user.role === 'user' && user.client_ids.map(id => clientsById.get(id)?.name || id).join(', ')}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function roleLabel(role: UserRole) {
  return role === 'all_clients' ? 'All clients' : role === 'superadmin' ? 'Superadmin' : 'Client user'
}

function ClientSelect({ clients, selected, onChange }: {
  clients: AdminClient[]
  selected: string[]
  onChange: (ids: string[]) => void
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const selectedSet = useMemo(() => new Set(selected), [selected])
  const selectedClients = useMemo(
    () => selected.map(id => clients.find(client => client.id === id)).filter(Boolean) as AdminClient[],
    [clients, selected],
  )
  const filteredClients = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase()
    if (!needle) return clients
    return clients.filter(client =>
      `${client.name} ${client.id}`.toLocaleLowerCase().includes(needle),
    )
  }, [clients, query])

  useEffect(() => {
    function closeOnOutsideClick(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    return () => document.removeEventListener('mousedown', closeOnOutsideClick)
  }, [])

  function toggle(clientId: string) {
    onChange(selectedSet.has(clientId)
      ? selected.filter(id => id !== clientId)
      : [...selected, clientId])
    setQuery('')
  }

  return (
    <div className={`client-picker ${open ? 'open' : ''}`} ref={rootRef}>
      <div className="client-picker-control" onClick={() => setOpen(true)}>
        {selectedClients.map(client => (
          <span className="client-picker-chip" key={client.id} title={`ERP ID ${client.id}`}>
            {client.name}
            <button type="button" aria-label={`Remove ${client.name}`} onClick={event => {
              event.stopPropagation()
              toggle(client.id)
            }}>×</button>
          </span>
        ))}
        <input
          aria-label="Search clients"
          placeholder={selected.length ? 'Add another client…' : 'Search clients…'}
          value={query}
          onChange={event => {
            setQuery(event.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={event => {
            if (event.key === 'Escape') {
              setOpen(false)
              event.currentTarget.blur()
            }
          }}
        />
        <span className="client-picker-chevron" aria-hidden="true">⌄</span>
      </div>
      {open && (
        <div className="client-picker-menu">
          <div className="client-picker-hint">
            Search by client name or ERP ID · machine totals from ERP
          </div>
          <div className="client-picker-options" role="listbox" aria-multiselectable="true">
            {filteredClients.map(client => (
              <button
                type="button"
                role="option"
                aria-selected={selectedSet.has(client.id)}
                className={`client-picker-option ${selectedSet.has(client.id) ? 'selected' : ''}`}
                key={client.id}
                onClick={() => toggle(client.id)}
              >
                <span className="client-picker-check" aria-hidden="true">
                  {selectedSet.has(client.id) ? '✓' : ''}
                </span>
                <span className="client-picker-option-text">
                  <strong>{client.name}</strong>
                  <small>ERP ID {client.id} · {machineCountLabel(client.machine_count)}</small>
                </span>
              </button>
            ))}
            {!filteredClients.length && (
              <div className="client-picker-empty">No clients match “{query}”.</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function machineCountLabel(count: number) {
  return `${count} ${count === 1 ? 'machine' : 'machines'}`
}
