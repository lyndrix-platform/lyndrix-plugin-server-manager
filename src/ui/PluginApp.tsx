import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { serverManagerApi } from './lib/api'
import type {
  ServerRecord, ServerInput, Catalog, StatusOption,
} from './lib/api'

// ─── Shared style helpers ───────────────────────────────────────────────────

function inputStyle(): React.CSSProperties {
  return {
    width: '100%',
    padding: '0.4rem 0.6rem',
    fontSize: '0.8rem',
    borderRadius: 'var(--lx-radius-sm)',
    border: '1px solid var(--lx-border-soft)',
    background: 'var(--lx-elevated)',
    color: 'var(--lx-text)',
    outline: 'none',
    boxSizing: 'border-box',
  }
}

const cardStyle: React.CSSProperties = {
  background: 'var(--lx-surface)',
  border: '1px solid var(--lx-border-soft)',
  borderRadius: 'var(--lx-radius-md)',
  overflow: 'hidden',
}

function Button({
  label, onClick, disabled, variant = 'default', type = 'button',
}: {
  label: string
  onClick?: () => void
  disabled?: boolean
  variant?: 'default' | 'danger' | 'primary'
  type?: 'button' | 'submit'
}) {
  const accent = variant === 'danger' ? 'var(--lx-state-down)' : 'var(--lx-accent)'
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: '0.35rem 0.8rem',
        fontSize: '0.72rem',
        fontWeight: 600,
        border: `1px solid color-mix(in srgb, ${accent} 40%, transparent)`,
        borderRadius: 'var(--lx-radius-sm)',
        background: variant === 'primary'
          ? `color-mix(in srgb, ${accent} 20%, transparent)`
          : variant === 'danger'
            ? `color-mix(in srgb, ${accent} 10%, transparent)`
            : 'var(--lx-surface)',
        color: disabled ? 'var(--lx-text-muted)' : accent,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'opacity 0.15s',
      }}
    >
      {label}
    </button>
  )
}

function ErrorBox({ msg }: { msg: string }) {
  return (
    <div style={{
      padding: '0.75rem 1rem',
      borderRadius: 'var(--lx-radius-md)',
      background: 'color-mix(in srgb, var(--lx-state-down) 10%, transparent)',
      border: '1px solid color-mix(in srgb, var(--lx-state-down) 25%, transparent)',
      color: 'var(--lx-state-down)',
      fontSize: '0.8rem',
      marginBottom: '1rem',
    }}>
      {msg}
    </div>
  )
}

function NoticeBox({ msg }: { msg: string }) {
  return (
    <div style={{
      padding: '0.6rem 1rem',
      borderRadius: 'var(--lx-radius-md)',
      background: 'color-mix(in srgb, var(--lx-state-up) 10%, transparent)',
      border: '1px solid color-mix(in srgb, var(--lx-state-up) 25%, transparent)',
      color: 'var(--lx-state-up)',
      fontSize: '0.8rem',
      marginBottom: '1rem',
    }}>
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
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 4,
      fontSize: '0.65rem',
      fontWeight: 600,
      color,
      background: `color-mix(in srgb, ${color} 12%, transparent)`,
      border: `1px solid color-mix(in srgb, ${color} 30%, transparent)`,
      borderRadius: 'var(--lx-radius-sm)',
      padding: '2px 7px',
      letterSpacing: '0.04em',
      textTransform: 'uppercase',
    }}>
      <span style={{ width: 5, height: 5, borderRadius: '50%', background: color, flexShrink: 0 }} />
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
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.25rem' }}>
        <h1 style={{ margin: 0, fontSize: '1.125rem', fontWeight: 700, color: 'var(--lx-text)' }}>
          Server Manager
        </h1>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <Button label="Aktualisieren" onClick={() => void load()} disabled={loading} />
          <Button label="+ Server" onClick={onCreate} variant="primary" />
        </div>
      </div>

      {error && <ErrorBox msg={error} />}

      {/* Filters */}
      <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
        <input
          style={{ ...inputStyle(), flex: '1 1 200px' }}
          placeholder="Suche nach Name, Hostname, Tag…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select style={{ ...inputStyle(), flex: '0 0 170px' }} value={envFilter} onChange={(e) => setEnvFilter(e.target.value)}>
          <option value="">Alle Umgebungen</option>
          {(catalog?.environments ?? []).map((e) => (
            <option key={e.id} value={e.id}>{e.provider_label ? `${e.provider_label} · ${e.label}` : e.label}</option>
          ))}
        </select>
        <select style={{ ...inputStyle(), flex: '0 0 140px' }} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">Alle Status</option>
          {statuses.map((s) => (<option key={s.id} value={s.id}>{s.label || s.id}</option>))}
        </select>
        <select style={{ ...inputStyle(), flex: '0 0 140px' }} value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
          <option value="">Alle Typen</option>
          {(catalog?.server_types ?? []).map((t) => (<option key={t.id} value={t.id}>{t.label || t.id}</option>))}
        </select>
      </div>

      {loading && servers.length === 0 && (
        <div style={{ color: 'var(--lx-text-muted)', fontSize: '0.875rem', textAlign: 'center', padding: '3rem 0' }}>
          Lade Server…
        </div>
      )}

      {!loading && filtered.length === 0 && (
        <div style={{ ...cardStyle, padding: '2rem', textAlign: 'center', color: 'var(--lx-text-muted)', fontSize: '0.875rem' }}>
          Keine Server gefunden.
        </div>
      )}

      {filtered.length > 0 && (
        <div style={cardStyle}>
          {filtered.map((s) => (
            <div
              key={s.id}
              onClick={() => onEdit(s)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '0.75rem',
                padding: '0.6rem 1rem',
                borderBottom: '1px solid var(--lx-border-soft)',
                cursor: 'pointer',
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--lx-text)' }}>
                  {s.name}
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--lx-text-muted)', fontFamily: 'monospace' }}>
                  {s.hostname || '—'} · {s.environment_id} · {s.server_type}
                </div>
              </div>
              <StatusBadge status={s.status} statuses={statuses} />
              {confirmId === s.id ? (
                <span style={{ display: 'flex', gap: '0.4rem' }} onClick={(e) => e.stopPropagation()}>
                  <Button label="Wirklich löschen" variant="danger" onClick={() => void doDelete(s.id)} />
                  <Button label="Abbrechen" onClick={() => setConfirmId(null)} />
                </span>
              ) : (
                <button
                  onClick={(e) => { e.stopPropagation(); setConfirmId(s.id) }}
                  title="Löschen"
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    color: 'var(--lx-state-down)', fontSize: '1rem', lineHeight: 1,
                    padding: '0.2rem 0.4rem', borderRadius: 'var(--lx-radius-sm)', opacity: 0.7,
                  }}
                >
                  ×
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Server form (create / edit) ────────────────────────────────────────────

interface FormState {
  name: string
  hostname: string
  environment_id: string
  server_type: string
  status: string
  os_type: string
  tags: string
  notes: string
  cpu: string
  ram_gb: string
  storage_gb: string
}

function emptyForm(catalog: Catalog | null): FormState {
  return {
    name: '',
    hostname: '',
    environment_id: catalog?.environments[0]?.id ?? '',
    server_type: catalog?.server_types[0]?.id ?? '',
    status: catalog?.statuses[0]?.id ?? '',
    os_type: '',
    tags: '',
    notes: '',
    cpu: '',
    ram_gb: '',
    storage_gb: '',
  }
}

function ServerForm({
  serverId, onSaved, onCancel,
}: {
  serverId: number | null
  onSaved: () => void
  onCancel: () => void
}) {
  const catalog = useCatalog()
  const [form, setForm] = useState<FormState>(emptyForm(null))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(serverId === null)
  const isEdit = serverId !== null

  // Seed defaults from catalog once it loads (create mode only).
  useEffect(() => {
    if (!isEdit && catalog) {
      setForm((f) => ({
        ...f,
        environment_id: f.environment_id || (catalog.environments[0]?.id ?? ''),
        server_type: f.server_type || (catalog.server_types[0]?.id ?? ''),
        status: f.status || (catalog.statuses[0]?.id ?? ''),
      }))
    }
  }, [catalog, isEdit])

  // Load existing server for edit.
  useEffect(() => {
    if (serverId === null) return
    serverManagerApi.getServer(serverId).then(({ server }) => {
      const hp = server.hardware_profile || {}
      setForm({
        name: server.name,
        hostname: server.hostname ?? '',
        environment_id: server.environment_id,
        server_type: server.server_type,
        status: server.status,
        os_type: server.os_type ?? '',
        tags: (server.tags ?? []).join(', '),
        notes: server.notes ?? '',
        cpu: String((hp as Record<string, unknown>).vcpu_count ?? (hp as Record<string, unknown>).cpu_count ?? ''),
        ram_gb: String((hp as Record<string, unknown>).ram_gb ?? ''),
        storage_gb: String((hp as Record<string, unknown>).storage_gb ?? ''),
      })
      setLoaded(true)
    }).catch((err) => setError(err instanceof Error ? err.message : 'Laden fehlgeschlagen'))
  }, [serverId])

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function buildHardwareProfile(): Record<string, unknown> {
    const hp: Record<string, unknown> = {}
    const cpu = parseInt(form.cpu, 10)
    const ram = parseInt(form.ram_gb, 10)
    const storage = parseInt(form.storage_gb, 10)
    if (!isNaN(cpu) && cpu > 0) {
      hp[form.server_type === 'physical' ? 'cpu_count' : 'vcpu_count'] = cpu
    }
    if (!isNaN(ram) && ram > 0) hp.ram_gb = ram
    if (!isNaN(storage) && storage > 0) hp.storage_gb = storage
    return hp
  }

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const payload: ServerInput = {
        name: form.name.trim(),
        hostname: form.hostname.trim() || null,
        environment_id: form.environment_id,
        server_type: form.server_type,
        status: form.status,
        os_type: form.os_type || null,
        tags: form.tags.split(',').map((t) => t.trim()).filter(Boolean),
        notes: form.notes.trim() || null,
        hardware_profile: buildHardwareProfile(),
      }
      if (isEdit) {
        await serverManagerApi.updateServer(serverId as number, payload)
      } else {
        await serverManagerApi.createServer(payload)
      }
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Speichern fehlgeschlagen')
    } finally {
      setBusy(false)
    }
  }

  const labelStyle: React.CSSProperties = { fontSize: '0.72rem', color: 'var(--lx-text-muted)', display: 'block' }
  const fieldGap: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 3 }

  if (!loaded) {
    return (
      <div style={{ maxWidth: 720, margin: '0 auto', padding: '3rem 1.5rem', color: 'var(--lx-text-muted)', textAlign: 'center' }}>
        Lade…
      </div>
    )
  }

  return (
    <div style={{ maxWidth: 720, margin: '0 auto', padding: '1.5rem 1.5rem 3rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
        <a
          href="#"
          onClick={(e) => { e.preventDefault(); onCancel() }}
          style={{ fontSize: '0.75rem', color: 'var(--lx-text-muted)', textDecoration: 'none' }}
        >
          ← Zurück
        </a>
        <h1 style={{ margin: 0, fontSize: '1.125rem', fontWeight: 700, color: 'var(--lx-text)' }}>
          {isEdit ? 'Server bearbeiten' : 'Server anlegen'}
        </h1>
      </div>

      {error && <ErrorBox msg={error} />}

      <div style={{ ...cardStyle, padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.9rem' }}>
        <div style={fieldGap}>
          <label style={labelStyle}>Name *</label>
          <input style={inputStyle()} value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="srv-app-01" />
        </div>

        <div style={fieldGap}>
          <label style={labelStyle}>Hostname</label>
          <input style={inputStyle()} value={form.hostname} onChange={(e) => set('hostname', e.target.value)} placeholder="srv-app-01.example.com" />
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <div style={{ ...fieldGap, flex: '1 1 200px' }}>
            <label style={labelStyle}>Umgebung *</label>
            <select style={inputStyle()} value={form.environment_id} onChange={(e) => set('environment_id', e.target.value)}>
              {(catalog?.environments ?? []).map((e) => (
                <option key={e.id} value={e.id}>{e.provider_label ? `${e.provider_label} · ${e.label}` : e.label}</option>
              ))}
            </select>
          </div>
          <div style={{ ...fieldGap, flex: '1 1 140px' }}>
            <label style={labelStyle}>Server-Typ *</label>
            <select style={inputStyle()} value={form.server_type} onChange={(e) => set('server_type', e.target.value)}>
              {(catalog?.server_types ?? []).map((t) => (<option key={t.id} value={t.id}>{t.label || t.id}</option>))}
            </select>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <div style={{ ...fieldGap, flex: '1 1 140px' }}>
            <label style={labelStyle}>Status</label>
            <select style={inputStyle()} value={form.status} onChange={(e) => set('status', e.target.value)}>
              {(catalog?.statuses ?? []).map((s) => (<option key={s.id} value={s.id}>{s.label || s.id}</option>))}
            </select>
          </div>
          <div style={{ ...fieldGap, flex: '1 1 140px' }}>
            <label style={labelStyle}>OS-Typ</label>
            <select style={inputStyle()} value={form.os_type} onChange={(e) => set('os_type', e.target.value)}>
              <option value="">—</option>
              {(catalog?.os_types ?? []).map((o) => (<option key={o.id} value={o.id}>{o.label || o.id}</option>))}
            </select>
          </div>
        </div>

        <div style={fieldGap}>
          <label style={labelStyle}>Tags (kommagetrennt)</label>
          <input style={inputStyle()} value={form.tags} onChange={(e) => set('tags', e.target.value)} placeholder="prod, db, critical" />
        </div>

        {/* Minimal hardware section */}
        <div style={{
          borderTop: '1px solid var(--lx-border-soft)', paddingTop: '0.9rem',
          fontSize: '0.72rem', fontWeight: 600, color: 'var(--lx-text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em',
        }}>
          Hardware (vereinfacht)
        </div>
        <div style={{ fontSize: '0.68rem', color: 'var(--lx-text-muted)', marginTop: '-0.4rem' }}>
          Für die vollständige Konfiguration mit Regelprüfung den NiceGUI-Konfigurator verwenden.
        </div>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <div style={{ ...fieldGap, flex: '1 1 120px' }}>
            <label style={labelStyle}>{form.server_type === 'physical' ? 'Sockets' : 'vCPU'}</label>
            <input type="number" style={inputStyle()} value={form.cpu} onChange={(e) => set('cpu', e.target.value)} placeholder="4" />
          </div>
          <div style={{ ...fieldGap, flex: '1 1 120px' }}>
            <label style={labelStyle}>RAM (GB)</label>
            <input type="number" style={inputStyle()} value={form.ram_gb} onChange={(e) => set('ram_gb', e.target.value)} placeholder="16" />
          </div>
          <div style={{ ...fieldGap, flex: '1 1 120px' }}>
            <label style={labelStyle}>Storage (GB)</label>
            <input type="number" style={inputStyle()} value={form.storage_gb} onChange={(e) => set('storage_gb', e.target.value)} placeholder="500" />
          </div>
        </div>

        <div style={fieldGap}>
          <label style={labelStyle}>Notizen</label>
          <textarea
            style={{ ...inputStyle(), minHeight: 70, resize: 'vertical', fontFamily: 'inherit' }}
            value={form.notes}
            onChange={(e) => set('notes', e.target.value)}
          />
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', marginTop: '0.3rem' }}>
          <Button label="Abbrechen" onClick={onCancel} disabled={busy} />
          <Button
            label={isEdit ? 'Speichern' : 'Anlegen'}
            onClick={() => void save()}
            disabled={busy || !form.name.trim() || !form.environment_id || !form.server_type}
            variant="primary"
          />
        </div>
      </div>
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
        <a
          href="#"
          onClick={(e) => { e.preventDefault(); window.history.back() }}
          style={{ fontSize: '0.75rem', color: 'var(--lx-text-muted)', textDecoration: 'none' }}
        >
          ← Zurück
        </a>
        <h1 style={{ margin: 0, fontSize: '1.125rem', fontWeight: 700, color: 'var(--lx-text)' }}>
          Server Manager — Einstellungen
        </h1>
      </div>

      {error && <ErrorBox msg={error} />}
      {notice && <NoticeBox msg={notice} />}

      <div style={{ ...cardStyle, padding: '1.25rem', display: 'flex', flexDirection: 'column', gap: '0.75rem', marginBottom: '1.25rem' }}>
        <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--lx-text)' }}>Katalog-Konfiguration</div>
        <div style={{ fontSize: '0.72rem', color: 'var(--lx-text-muted)' }}>
          Verzeichnis mit hardware.yml, environments.yml, profiles.yml, products.yml, settings.yml.
          Leer lassen für den mitgelieferten Standard-Katalog.
        </div>
        <input
          style={inputStyle()}
          value={pathInput}
          onChange={(e) => setPathInput(e.target.value)}
          placeholder={catalog?.is_default ? `Standard: ${catalog?.catalog_dir ?? ''}` : ''}
        />
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <Button label="Pfad übernehmen" onClick={() => void applyPath()} disabled={busy} variant="primary" />
          <Button label="Katalog neu laden" onClick={() => void reload()} disabled={busy} />
        </div>
        <div style={{ fontSize: '0.68rem', color: 'var(--lx-text-muted)', fontFamily: 'monospace' }}>
          Aktiv: {catalog?.catalog_dir ?? '—'} {catalog?.is_default ? '(Standard)' : ''}
        </div>
      </div>

      {/* Catalog stats */}
      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
        {statEntries.map(([key, label]) => (
          <div key={key} style={{ ...cardStyle, padding: '0.75rem 1rem', minWidth: 110, flex: '1 1 110px' }}>
            <div style={{ fontSize: '0.68rem', color: 'var(--lx-text-muted)' }}>{label}</div>
            <div style={{ fontSize: '1.5rem', fontWeight: 700, color: 'var(--lx-text)' }}>{stats[key] ?? 0}</div>
          </div>
        ))}
      </div>

      {/* Products preview */}
      {catalog && catalog.products.length > 0 && (
        <div style={{ ...cardStyle, marginTop: '1.25rem' }}>
          <div style={{ padding: '0.6rem 1rem', borderBottom: '1px solid var(--lx-border-soft)', background: 'var(--lx-elevated)', fontSize: '0.8rem', fontWeight: 600, color: 'var(--lx-text)' }}>
            Produkte
          </div>
          {catalog.products.map((p) => (
            <div key={p.id} style={{ padding: '0.5rem 1rem', borderBottom: '1px solid var(--lx-border-soft)', fontSize: '0.75rem', color: 'var(--lx-text)' }}>
              <span style={{ fontWeight: 600 }}>{p.label}</span>
              <span style={{ color: 'var(--lx-text-muted)', fontFamily: 'monospace', marginLeft: 6 }}>[{p.id}]</span>
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

  if (view.kind === 'form') {
    return (
      <ServerForm
        serverId={view.id}
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
