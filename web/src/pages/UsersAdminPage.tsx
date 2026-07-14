import { useEffect, useMemo, useState, type FormEvent } from 'react'
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
  const [email, setEmail] = useState('')
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
        email,
        name,
        role,
        client_ids: role === 'user' ? clientIds : [],
      })
      setTemporaryPassword(result.temporary_password)
      setEmail('')
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
    if (!window.confirm(`Reset password for ${user.email}?`)) return
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
        <input type="email" placeholder="Email" value={email} onChange={e => setEmail(e.target.value)} required />
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
              <span>{user.email}</span>
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
  return (
    <select multiple value={selected} onChange={event => {
      onChange(Array.from(event.currentTarget.selectedOptions, option => option.value))
    }}>
      {clients.map(client => (
        <option key={client.id} value={client.id}>{client.name} ({client.machine_count})</option>
      ))}
    </select>
  )
}
