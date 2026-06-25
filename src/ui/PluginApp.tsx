import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { serverManagerApi } from './lib/api'
import type {
  ServerRecord, Catalog, StatusOption,
} from './lib/api'
import ServerWizard from './ServerWizard'

// ─── Shared style helpers ───────────────────────────────────────────────────

const cardStyle: React.CSSProperties = {
  background: 'var(--lx-surface)',
  border: '1px solid var(--lx-border-soft)',
  borderRadius: 'var(--lx-radius-md)',
  overflow: 'hidden',
}

function Button({
  label, onClick, disabled, variant = 'default', type = 'button', icon,
}: {
  label: string
  onClick?: () => void
  disabled?: boolean
  variant?: 'default' | 'danger' | 'primary'
  type?: 'button' | 'submit'
  icon?: string
}) {
  const cls =
    variant === 'primary' ? 'lx-btn lx-btn--primary lx-btn--sm'
    : variant === 'danger' ? 'lx-btn lx-btn--danger lx-btn--sm'
    : 'lx-btn lx-btn--secondary lx-btn--sm'
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={cls}>
      {icon && <span className="material-icons" style={{ fontSize: 16 }}>{icon}</span>}
      {label}
    </button>
  )
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '0.7rem 1rem',
      borderRadius: 'var(--lx-radius-md)',
      background: 'color-mix(in srgb, var(--lx-state-down) 10%, transparent)',
      border: '1px solid color-mix(in srgb, var(--lx-state-down) 25%, transparent)',
      color: 'var(--lx-state-down)',
      fontSize: '0.8rem',
      marginBottom: '1rem',
    }}>
      <span className="material-icons" style={{ fontSize: 18 }}>error_outline</span>
      {msg}
    </div>
  )
}

function NoticeBox({ msg }: { msg: string }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '0.7rem 1rem',
      borderRadius: 'var(--lx-radius-md)',
      background: 'color-mix(in srgb, var(--lx-state-up) 10%, transparent)',
      border: '1px solid color-mix(in srgb, var(--lx-state-up) 25%, transparent)',
      color: 'var(--lx-state-up)',
      fontSize: '0.8rem',
      marginBottom: '1rem',
    }}>
      <span className="material-icons" style={{ fontSize: 18 }}>check_circle</span>
      {msg}
    </div>
  )
}

function statusColor(status: string, statuses: StatusOption[]): string {
  const s = statuses.find((x) => x.id === status)
  const c = (s?.color || '').toLowerCase()
  if (['green', 'positive', 'success'].includes(c)) return 'var(--lx-state-up)'
  if (['red', 'negative', 'error'].includes(c)) return 'var(--lx-state-down)'
  if (['amber', 'orange', 'warning', 'yellow'].includes(c)) return 'var(--lx-state-paused)'
  return 'var(--lx-state-unknown)'
}

function StatusBadge({ status, statuses }: { status: string; statuses: StatusOption[] }) {
  const color = statusColor(status, statuses)
  const label = statuses.find((x) => x.id === status)?.label || status
  return (
    <span className="lx-badge" style={{
      color,
      background: `color-mix(in srgb, ${color} 13%, transparent)`,
      borderColor: `color-mix(in srgb, ${color} 28%, transparent)`,
    }}>
      <span className="lx-dot" />
      {label}
    </span>
  )
}

function useCatalog() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  useEffect(() => {
    serverManagerApi.getCatalog().then(setCatalog).catch(() => setCatalog(null))
  }, [])
  return catalog
}

// ─── Server list ────────────────────────────────────────────────────────────

function ServerListView({
  onCreate, onEdit,
}: {
  onCreate: () => void
  onEdit: (s: ServerRecord) => void
}) {
  const catalog = useCatalog()
  const [servers, setServers] = useState<ServerRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [envFilter, setEnvFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [confirmId, setConfirmId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await serverManagerApi.listServers()
      setServers(res.servers)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Fehler beim Laden')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const statuses = catalog?.statuses ?? []

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return servers.filter((s) => {
      if (envFilter && s.environment_id !== envFilter) return false
      if (statusFilter && s.status !== statusFilter) return false
      if (typeFilter && s.server_type !== typeFilter) return false
      if (q) {
        const hay = `${s.name} ${s.hostname ?? ''} ${(s.tags ?? []).join(' ')}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [servers, search, envFilter, statusFilter, typeFilter])

  async function doDelete(id: number) {
    try {
      await serverManagerApi.deleteServer(id)
      setConfirmId(null)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Löschen fehlgeschlagen')
    }
  }

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto', padding: '1.5rem 1.5rem 3rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem', marginBottom: '0.35rem' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: '1.25rem', fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--lx-text)' }}>
            Server Manager
          </h1>
          <p style={{ margin: '0.25rem 0 0', fontSize: '0.8rem', color: 'var(--lx-text-muted)' }}>
            {filtered.length} {filtered.length === 1 ? 'Server' : 'Server'} · Verwalte und konfiguriere deine Server.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0 }}>
          <Button label="Aktualisieren" icon="refresh" onClick={() => void load()} disabled={loading} />
          <Button label="Server" icon="add" onClick={onCreate} variant="primary" />
        </div>
      </div>

      <div style={{ marginTop: '1.25rem' }}>{error && <ErrorBox msg={error} />}</div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
        <input
          className="lx-input"
          style={{ flex: '1 1 200px' }}
          placeholder="Suche nach Name, Hostname, Tag…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className="lx-select" style={{ flex: '0 0 170px' }} value={envFilter} onChange={(e) => setEnvFilter(e.target.value)}>
          <option value="">Alle Umgebungen</option>
          {(catalog?.environments ?? []).map((e) => (
            <option key={e.id} value={e.id}>{e.provider_label ? `${e.provider_label} · ${e.label}` : e.label}</option>
          ))}
        </select>
        <select className="lx-select" style={{ flex: '0 0 140px' }} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">Alle Status</option>
          {statuses.map((s) => (<option key={s.id} value={s.id}>{s.label || s.id}</option>))}
        </select>
        <select className="lx-select" style={{ flex: '0 0 140px' }} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">Alle Typen</option>
          {(catalog?.server_types ?? []).map((t) => (<option key={t.id} value={t.id}>{t.label || t.id}</option>))}
        </select>
      </div>

      {loading && servers.length === 0 && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '3rem 0' }}>
          <span className="lx-spinner" />
        </div>
      )}

      {!loading && filtered.length === 0 && (
        <div className="lx-card lx-empty">
          <span className="material-icons">dns</span>
          <p style={{ fontSize: '0.875rem', margin: 0 }}>Keine Server gefunden.</p>
          <button className="lx-btn lx-btn--secondary lx-btn--sm" style={{ marginTop: 4 }} onClick={onCreate}>
            <span className="material-icons" style={{ fontSize: 16 }}>add</span>
            Ersten Server anlegen
          </button>
        </div>
      )}

      {filtered.length > 0 && (
        <div style={cardStyle}>
          <table className="lx-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Verbindung</th>
                <th>Status</th>
                <th style={{ width: 44 }} aria-label="Aktionen" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => (
                <tr key={s.id} onClick={() => onEdit(s)} style={{ cursor: 'pointer' }}>
                  <td>
                    <span style={{ fontWeight: 600, color: 'var(--lx-text)' }}>{s.name}</span>
                  </td>
                  <td>
                    <span className="lx-mono" style={{ fontSize: '0.72rem', color: 'var(--lx-text-muted)' }}>
                      {s.hostname || '—'} · {s.environment_id} · {s.server_type}
                    </span>
                  </td>
                  <td><StatusBadge status={s.status} statuses={statuses} /></td>
                  <td style={{ textAlign: 'right' }} onClick={(e) => e.stopPropagation()}>
                    {confirmId === s.id ? (
                      <span style={{ display: 'inline-flex', gap: '0.4rem' }}>
                        <Button label="Löschen" variant="danger" onClick={() => void doDelete(s.id)} />
                        <Button label="Abbrechen" onClick={() => setConfirmId(null)} />
                      </span>
                    ) : (
                      <button
                        onClick={() => setConfirmId(s.id)}
                        title="Löschen"
                        className="lx-icon-btn lx-icon-btn--danger"
                      >
                        <span className="material-icons" style={{ fontSize: 18 }}>delete_outline</span>
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ─── Settings view ──────────────────────────────────────────────────────────

function SettingsView() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [pathInput, setPathInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const cat = await serverManagerApi.getCatalog()
      setCatalog(cat)
      setPathInput(cat.is_default ? '' : cat.catalog_dir)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Fehler beim Laden')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  async function applyPath() {
    setBusy(true); setError(null); setNotice(null)
    try {
      const cat = await serverManagerApi.setCatalogPath(pathInput.trim())
      setCatalog(cat)
      setNotice(cat.is_default ? 'Auf Standard-Katalog zurückgesetzt.' : `Katalogpfad gesetzt: ${cat.catalog_dir}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Pfad konnte nicht gesetzt werden')
    } finally { setBusy(false) }
  }

  async function reload() {
    setBusy(true); setError(null); setNotice(null)
    try {
      const cat = await serverManagerApi.reloadCatalog()
      setCatalog(cat)
      setNotice('Katalog neu geladen.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reload fehlgeschlagen')
    } finally { setBusy(false) }
  }

  const stats = catalog?.stats ?? {}
  const statEntries: [string, string][] = [
    ['cpu_options', 'CPU-Optionen'],
    ['ram_options', 'RAM-Module'],
    ['storage_options', 'Storage-Optionen'],
    ['profiles', 'Profile'],
    ['products', 'Produkte'],
    ['providers', 'Provider'],
    ['stages', 'Stages'],
    ['legacy_rules', 'Legacy-Regeln'],
  ]

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '1.5rem 1.5rem 3rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
        <button
          onClick={() => window.history.back()}
          className="lx-icon-btn"
          title="Zurück"
        >
          <span className="material-icons" style={{ fontSize: 18 }}>arrow_back</span>
        </button>
        <h1 style={{ margin: 0, fontSize: '1.25rem', fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--lx-text)' }}>
          Server Manager — Einstellungen
        </h1>
      </div>

      {error && <ErrorBox msg={error} />}
      {notice && <NoticeBox msg={notice} />}

      <div style={{ ...cardStyle, padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem', marginBottom: '1.25rem' }}>
        <div className="lx-section-title">Katalog-Konfiguration</div>
        <div style={{ fontSize: '0.78rem', color: 'var(--lx-text-muted)', lineHeight: 1.5 }}>
          Verzeichnis mit hardware.yml, environments.yml, profiles.yml, products.yml, settings.yml.
          Leer lassen für den mitgelieferten Standard-Katalog.
        </div>
        <input
          className="lx-input"
          value={pathInput}
          onChange={(e) => setPathInput(e.target.value)}
          placeholder={catalog?.is_default ? `Standard: ${catalog?.catalog_dir ?? ''}` : ''}
        />
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <Button label="Pfad übernehmen" icon="save" onClick={() => void applyPath()} disabled={busy} variant="primary" />
          <Button label="Katalog neu laden" icon="refresh" onClick={() => void reload()} disabled={busy} />
        </div>
        <div className="lx-mono" style={{ fontSize: '0.7rem', color: 'var(--lx-text-muted)' }}>
          Aktiv: {catalog?.catalog_dir ?? '—'} {catalog?.is_default ? '(Standard)' : ''}
        </div>
      </div>

      {/* Catalog stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: '0.75rem' }}>
        {statEntries.map(([key, label]) => (
          <div key={key} className="lx-card lx-card-hover" style={{ padding: '0.9rem 1rem' }}>
            <div style={{ fontSize: '0.7rem', color: 'var(--lx-text-muted)' }}>{label}</div>
            <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--lx-text)', marginTop: 2 }}>{stats[key] ?? 0}</div>
          </div>
        ))}
      </div>

      {/* Products preview */}
      {catalog && catalog.products.length > 0 && (
        <div style={{ ...cardStyle, marginTop: '1.25rem' }}>
          <div style={{ padding: '0.75rem 1rem', borderBottom: '1px solid var(--lx-border-soft)', background: 'var(--lx-elevated)' }} className="lx-section-title">
            Produkte
          </div>
          {catalog.products.map((p) => (
            <div key={p.id} style={{ padding: '0.6rem 1rem', borderBottom: '1px solid var(--lx-border-soft)', fontSize: '0.8rem', color: 'var(--lx-text)' }}>
              <span style={{ fontWeight: 600 }}>{p.label}</span>
              <span className="lx-mono" style={{ color: 'var(--lx-text-muted)', marginLeft: 6, fontSize: '0.72rem' }}>[{p.id}]</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Root ───────────────────────────────────────────────────────────────────

function ServerManagerView() {
  const [view, setView] = useState<{ kind: 'list' } | { kind: 'form'; id: number | null }>({ kind: 'list' })
  const catalog = useCatalog()

  if (view.kind === 'form') {
    if (!catalog) {
      return <div style={{ maxWidth: 720, margin: '0 auto', padding: '3rem 1.5rem', color: 'var(--lx-text-muted)', textAlign: 'center' }}>Lade Katalog…</div>
    }
    return (
      <ServerWizard
        serverId={view.id}
        catalog={catalog}
        onSaved={() => setView({ kind: 'list' })}
        onCancel={() => setView({ kind: 'list' })}
      />
    )
  }
  return (
    <ServerListView
      onCreate={() => setView({ kind: 'form', id: null })}
      onEdit={(s) => setView({ kind: 'form', id: s.id })}
    />
  )
}

export default function PluginApp() {
  const isSettings = window.location.pathname.endsWith('/settings')
  return isSettings ? <SettingsView /> : <ServerManagerView />
}
