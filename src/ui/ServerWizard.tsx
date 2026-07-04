import React, { useCallback, useEffect, useMemo, useState } from 'react'
// react-i18next is provided by the host shell (window.__lyndrix_react_i18next);
// declared external in vite.ui.config.ts so the plugin shares the host's i18n
// instance + active language. Strings come from locales/server_manager.<locale>.json,
// auto-registered by core and served via the catalog (namespace "server_manager").
import { useTranslation } from 'react-i18next'
import { serverManagerApi } from './lib/api'
import type {
  Catalog, Product, OsVariant, StorageOption,
  ServerInput, ComboVerdict, ValidationIssue,
} from './lib/api'

// ─── Shared styles (var(--lx-*) theme tokens) ───────────────────────────────

// Card-like panels carry the host `lx-card` class so they pick up the host's
// liquid-glass refraction. The base glass surface (background/border/blur) comes
// from the host stylesheet — only structural layout stays inline here.
const card: React.CSSProperties = {
  borderRadius: 'var(--lx-radius-md)',
  padding: '1rem',
  display: 'flex',
  flexDirection: 'column',
  gap: '0.75rem',
}

const fieldLabel: React.CSSProperties = {
  fontSize: '0.75rem',
  fontWeight: 500,
  color: 'var(--lx-text-muted)',
}

type TileState = 'normal' | 'selected' | 'disabled' | 'special'

function tileStyle(state: TileState): React.CSSProperties {
  const base: React.CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: 2,
    padding: '0.5rem 0.7rem',
    minWidth: 56,
    borderRadius: 'var(--lx-radius-md)',
    fontSize: '0.72rem',
    fontWeight: 600,
    lineHeight: 1.2,
    cursor: 'pointer',
    userSelect: 'none',
    transition: `all var(--lx-transition-fast) var(--lx-ease)`,
    border: '1px solid var(--lx-border-soft)',
    background: 'var(--lx-surface-glass, var(--lx-surface))',
    backdropFilter: 'blur(var(--lx-glass-blur)) saturate(var(--lx-glass-saturate))',
    WebkitBackdropFilter: 'blur(var(--lx-glass-blur)) saturate(var(--lx-glass-saturate))',
    color: 'var(--lx-text)',
  }
  if (state === 'selected') {
    return { ...base, border: '1px solid var(--lx-accent)', background: 'color-mix(in srgb, var(--lx-accent) 16%, transparent)', color: 'var(--lx-accent)' }
  }
  if (state === 'disabled') {
    return { ...base, opacity: 0.38, cursor: 'not-allowed', color: 'var(--lx-text-muted)' }
  }
  if (state === 'special') {
    return { ...base, border: '1px solid var(--lx-state-paused)', background: 'color-mix(in srgb, var(--lx-state-paused) 14%, transparent)', color: 'var(--lx-state-paused)' }
  }
  return base
}

function Tile({ state, onClick, children, style }: {
  state: TileState
  onClick?: () => void
  children: React.ReactNode
  style?: React.CSSProperties
}) {
  return (
    <div
      style={{ ...tileStyle(state), ...style }}
      onClick={state === 'disabled' ? undefined : onClick}
    >
      {children}
    </div>
  )
}

function Btn({ label, onClick, disabled, variant = 'default', icon }: {
  label: string
  onClick?: () => void
  disabled?: boolean
  variant?: 'default' | 'primary' | 'positive'
  icon?: string
}) {
  const cls =
    variant === 'default' ? 'lx-btn lx-btn--secondary' : 'lx-btn lx-btn--primary'
  return (
    <button type="button" onClick={onClick} disabled={disabled} className={cls}>
      {icon === 'left' && <span className="material-icons" style={{ fontSize: 16 }}>arrow_back</span>}
      {label}
      {icon === 'right' && <span className="material-icons" style={{ fontSize: 16 }}>arrow_forward</span>}
      {icon === 'check' && <span className="material-icons" style={{ fontSize: 16 }}>check</span>}
    </button>
  )
}

const tileRow: React.CSSProperties = { display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'stretch' }

// Material Icons glyph (font loaded globally by the shell). Renders nothing when no icon.
function Ico({ name, size = 18 }: { name?: string | null; size?: number }) {
  if (!name) return null
  return (
    <span className="material-icons" style={{ fontSize: size, lineHeight: 1, flexShrink: 0 }} aria-hidden>
      {name}
    </span>
  )
}

// ─── Form state ─────────────────────────────────────────────────────────────

export interface StorageDisk {
  disk_id: string
  mount: string
  size_gb: number
}

interface WizardForm {
  name: string
  hostname: string
  provider_id: string
  environment_id: string
  server_type: string
  product_id: string
  service_class_id: string
  os_family_id: string
  os_version_id: string
  os_type: string
  status: string
  tags: string
  notes: string
  cpu_id: string
  cpu_count: number
  vcpu_count: number | null
  ram_gb: number | null
  storage_disks: StorageDisk[]
}

function blankForm(): WizardForm {
  return {
    name: '', hostname: '',
    provider_id: '', environment_id: '', server_type: '',
    product_id: '', service_class_id: '',
    os_family_id: '', os_version_id: '', os_type: '',
    status: 'active', tags: '', notes: '',
    cpu_id: '', cpu_count: 1,
    vcpu_count: null, ram_gb: null, storage_disks: [],
  }
}


// ─── Wizard ─────────────────────────────────────────────────────────────────

export default function ServerWizard({ serverId, catalog, onSaved, onCancel }: {
  serverId: number | null
  catalog: Catalog
  onSaved: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation('server_manager')
  const isEdit = serverId !== null
  const stepLabels = [
    t('wizard.steps.basics', { defaultValue: 'Basics & product' }),
    t('wizard.steps.hardware', { defaultValue: 'Hardware' }),
    t('wizard.steps.review', { defaultValue: 'Review & save' }),
  ]
  const [form, setForm] = useState<WizardForm>(blankForm)
  const [step, setStep] = useState(0)
  const [loaded, setLoaded] = useState(serverId === null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // ── Edit seeding ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (serverId === null) { setForm((f) => ({ ...f, status: catalog.statuses[0]?.id ?? 'active' })); return }
    serverManagerApi.getServer(serverId).then(({ server }) => {
      const hp = (server.hardware_profile || {}) as Record<string, unknown>
      const envId = server.environment_id || ''
      let disks = Array.isArray(hp.storage_disks) ? (hp.storage_disks as StorageDisk[]) : []
      if (disks.length === 0 && hp.storage_gb) {
        disks = [{ disk_id: 'virtual-disk', mount: '/', size_gb: Number(hp.storage_gb) }]
      }
      setForm({
        name: server.name,
        hostname: server.hostname ?? '',
        provider_id: envId.includes(':') ? envId.split(':')[0] : '',
        environment_id: envId,
        server_type: server.server_type || '',
        product_id: server.product_id ?? '',
        service_class_id: server.service_class_id ?? '',
        os_family_id: server.os_family_id ?? '',
        os_version_id: server.os_version_id ?? '',
        os_type: server.os_type ?? '',
        status: server.status,
        tags: (server.tags ?? []).join(', '),
        notes: server.notes ?? '',
        cpu_id: typeof hp.cpu_id === 'string' ? hp.cpu_id : '',
        cpu_count: Number(hp.cpu_count ?? 1),
        vcpu_count: hp.vcpu_count != null ? Number(hp.vcpu_count) : null,
        ram_gb: hp.ram_gb != null ? Number(hp.ram_gb) : null,
        storage_disks: disks.map((d) => ({ disk_id: d.disk_id ?? '', mount: d.mount ?? '', size_gb: Number(d.size_gb ?? 0) })),
      })
      setLoaded(true)
    }).catch((e) => { setError(e instanceof Error ? e.message : t('common.loadFailed', { defaultValue: 'Failed to load' })); setLoaded(true) })
  }, [serverId, catalog, t])

  // ── Derived catalog data ───────────────────────────────────────────────────
  const provider = useMemo(() => catalog.providers.find((p) => p.id === form.provider_id) ?? null, [catalog, form.provider_id])
  const profileId = provider?.profile_id ?? ''
  const profile = useMemo(() => catalog.profiles.find((p) => p.id === profileId) ?? null, [catalog, profileId])
  const isPhysical = form.server_type === 'physical'
  const selectedProduct = useMemo(() => catalog.products.find((p) => p.id === form.product_id) ?? null, [catalog, form.product_id])

  const familyOsType = useMemo(() => {
    const m: Record<string, string> = {}
    catalog.products.forEach((p) => p.os_variants.forEach((v) => { if (v.os_type && !(v.id in m)) m[v.id] = v.os_type }))
    return m
  }, [catalog])

  const baseProducts = useMemo(() => {
    if (!profile || !form.server_type) return [] as Product[]
    return catalog.products.filter((p) =>
      p.compatible_profiles.includes(profile.id) && p.compatible_server_types.includes(form.server_type))
  }, [catalog, profile, form.server_type])

  const osFamilies = useMemo(() => {
    const seen = new Map<string, OsVariant>()
    baseProducts.forEach((p) => p.os_variants.forEach((v) => { if (!seen.has(v.id)) seen.set(v.id, v) }))
    return Array.from(seen.values())
  }, [baseProducts])

  const productsForSel = useMemo(() => {
    let list = baseProducts
    if (form.os_family_id) {
      list = list.filter((p) => p.os_variants.length === 0 || p.os_variants.some((v) => v.id === form.os_family_id))
    }
    return list
  }, [baseProducts, form.os_family_id])

  // ── Cascade selectors (each resets downstream fields) ──────────────────────
  const pickProvider = (pid: string) => setForm((f) => ({
    ...f, provider_id: pid, environment_id: '', server_type: '',
    product_id: '', service_class_id: '', os_family_id: '', os_version_id: '', os_type: '',
  }))
  const pickStage = (envId: string) => setForm((f) => ({
    ...f, environment_id: envId, server_type: '',
    product_id: '', service_class_id: '', os_family_id: '', os_version_id: '', os_type: '',
  }))
  const pickType = (tid: string) => setForm((f) => ({
    ...f, server_type: tid,
    product_id: '', service_class_id: '', os_family_id: '', os_version_id: '', os_type: '',
  }))
  const pickOsFamily = (fam: string) => setForm((f) => ({
    ...f, os_family_id: fam, os_version_id: '', os_type: familyOsType[fam] ?? '',
    product_id: '', service_class_id: '',
  }))
  const selectProduct = useCallback((pid: string) => setForm((f) => {
    const prod = catalog.products.find((p) => p.id === pid)
    const scs = prod?.service_classes ?? []
    let osv = ''
    if (f.os_family_id && prod) {
      const variant = prod.os_variants.find((v) => v.id === f.os_family_id)
      const versions = variant?.os_versions ?? []
      const pref = versions.find((v) => v.lifecycle === 'preferred') ?? versions[0]
      osv = pref?.id ?? ''
    }
    return { ...f, product_id: pid, service_class_id: scs[0]?.id ?? '', os_version_id: osv }
  }), [catalog])

  // Auto-select server_type when the profile allows exactly one.
  useEffect(() => {
    if (form.environment_id && !form.server_type && profile && profile.allowed_server_types.length === 1) {
      setForm((f) => ({ ...f, server_type: profile.allowed_server_types[0] }))
    }
  }, [form.environment_id, form.server_type, profile])

  // Auto-select the product when only one matches.
  useEffect(() => {
    if (form.server_type && form.environment_id && !form.product_id && productsForSel.length === 1) {
      selectProduct(productsForSel[0].id)
    }
  }, [form.server_type, form.environment_id, form.product_id, productsForSel, selectProduct])

  // ── Validation (step 3) ────────────────────────────────────────────────────
  const buildProfile = useCallback((): Record<string, unknown> => {
    const base: Record<string, unknown> = { os_family_id: form.os_family_id || null }
    if (isPhysical) {
      return {
        ...base,
        cpu_id: form.cpu_id || null,
        cpu_count: Number(form.cpu_count || 1),
        ram_gb: form.ram_gb || null,
        storage_disks: form.storage_disks,
      }
    }
    return {
      ...base,
      vcpu_count: form.vcpu_count || null,
      ram_gb: form.ram_gb || null,
      storage_disks: form.storage_disks,
    }
  }, [form, isPhysical])

  const [issues, setIssues] = useState<ValidationIssue[]>([])
  useEffect(() => {
    if (step !== 2) return
    let cancel = false
    serverManagerApi.validateServer({
      server_type: form.server_type,
      profile: buildProfile(),
      profile_id: profileId || null,
      product_id: form.product_id || null,
      environment_id: form.environment_id || null,
      os_type: form.os_type || null,
      os_family_id: form.os_family_id || null,
    }).then((r) => { if (!cancel) setIssues(r.issues) })
      .catch(() => { if (!cancel) setIssues([]) })
    return () => { cancel = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, form, profileId, buildProfile])

  const hasError = issues.some((i) => i.severity === 'error')

  async function save() {
    setBusy(true); setError(null)
    const payload: ServerInput = {
      name: form.name.trim(),
      hostname: form.hostname.trim() || null,
      environment_id: form.environment_id,
      server_type: form.server_type,
      product_id: form.product_id || null,
      service_class_id: form.service_class_id || null,
      os_type: form.os_type || null,
      os_family_id: form.os_family_id || null,
      os_version_id: form.os_version_id || null,
      status: form.status,
      tags: form.tags.split(',').map((t) => t.trim()).filter(Boolean),
      notes: form.notes.trim() || null,
      hardware_profile: buildProfile(),
    }
    try {
      if (isEdit) await serverManagerApi.updateServer(serverId as number, payload)
      else await serverManagerApi.createServer(payload)
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : t('common.saveFailed', { defaultValue: 'Save failed' }))
    } finally { setBusy(false) }
  }

  if (!loaded) {
    return <div style={{ padding: '3rem', textAlign: 'center', color: 'var(--lx-text-muted)' }}>{t('common.loading', { defaultValue: 'Loading…' })}</div>
  }

  const setField = <K extends keyof WizardForm>(k: K, v: WizardForm[K]) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <div style={{ maxWidth: 920, margin: '0 auto', padding: '1.25rem 1.5rem 3rem' }}>
      {/* Header + step bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.25rem' }}>
        <button onClick={onCancel} className="lx-icon-btn" title={t('common.back', { defaultValue: 'Back' })}>
          <span className="material-icons" style={{ fontSize: 18 }}>arrow_back</span>
        </button>
        <h1 style={{ margin: 0, fontSize: '1.25rem', fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--lx-text)' }}>
          {isEdit ? t('wizard.editTitle', { defaultValue: 'Edit: {{name}}', name: form.name }) : t('wizard.createTitle', { defaultValue: 'Create new server' })}
        </h1>
      </div>

      {/* Shared `.lx-stepper` component (Theming v2 T0b) — the exact same
          classes the NiceGUI configurator's step bar uses, so the two GUIs
          stop drifting on step-indicator styling. */}
      <div className="lx-stepper" style={{ marginBottom: '1.5rem' }}>
        {stepLabels.map((lbl, i) => {
          const active = i === step
          const done = i < step
          const labelColor = active ? 'var(--lx-accent)' : done ? 'var(--lx-state-success)' : 'var(--lx-text-muted)'
          const stepCls = `lx-stepper__step${active ? ' lx-stepper__step--active' : done ? ' lx-stepper__step--done' : ''}`
          return (
            <React.Fragment key={lbl}>
              <div
                onClick={() => setStep(i)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
                  fontSize: '0.8rem', fontWeight: 600, color: labelColor,
                }}
              >
                <span className={stepCls}>
                  {done ? <span className="material-icons" style={{ fontSize: 16 }}>check</span> : i + 1}
                </span>
                <span style={{ display: window.innerWidth < 560 ? 'none' : 'inline' }}>{lbl}</span>
              </div>
              {i < stepLabels.length - 1 && <div className="lx-stepper__bar" />}
            </React.Fragment>
          )
        })}
      </div>

      {error && (
        <div style={{ padding: '0.6rem 1rem', borderRadius: 'var(--lx-radius-md)', marginBottom: '1rem', fontSize: '0.78rem', color: 'var(--lx-state-down)', background: 'color-mix(in srgb, var(--lx-state-down) 10%, transparent)', border: '1px solid color-mix(in srgb, var(--lx-state-down) 25%, transparent)' }}>{error}</div>
      )}

      {step === 0 && (
        <Step1
          catalog={catalog} form={form} setField={setField}
          provider={provider} profile={profile}
          osFamilies={osFamilies} productsForSel={productsForSel} selectedProduct={selectedProduct}
          pickProvider={pickProvider} pickStage={pickStage} pickType={pickType}
          pickOsFamily={pickOsFamily} selectProduct={selectProduct}
        />
      )}

      {step === 1 && (
        <Step2
          catalog={catalog} form={form} setForm={setForm}
          profile={profile} selectedProduct={selectedProduct} isPhysical={isPhysical} step={step}
        />
      )}

      {step === 2 && (
        <Step3 catalog={catalog} form={form} issues={issues} isPhysical={isPhysical} />
      )}

      {/* Nav */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '1.75rem', paddingTop: '1.25rem', borderTop: '1px solid var(--lx-border-soft)' }}>
        <Btn label={step === 0 ? t('common.cancel', { defaultValue: 'Cancel' }) : t('common.back', { defaultValue: 'Back' })} icon={step === 0 ? undefined : 'left'} onClick={() => (step === 0 ? onCancel() : setStep(step - 1))} />
        {step < 2
          ? <Btn label={t('wizard.next', { defaultValue: 'Next' })} icon="right" variant="primary" onClick={() => setStep(step + 1)} disabled={step === 0 && (!form.name.trim() || !form.environment_id || !form.server_type)} />
          : <Btn label={isEdit ? t('common.save', { defaultValue: 'Save' }) : t('common.create', { defaultValue: 'Create' })} icon="check" variant="primary" onClick={() => void save()} disabled={busy || hasError || !form.name.trim() || !form.environment_id || !form.server_type} />}
      </div>
    </div>
  )
}

// ─── Step 1 — Basis & Produkt ────────────────────────────────────────────────

function Step1({
  catalog, form, setField, provider, profile, osFamilies, productsForSel, selectedProduct,
  pickProvider, pickStage, pickType, pickOsFamily, selectProduct,
}: {
  catalog: Catalog
  form: WizardForm
  setField: <K extends keyof WizardForm>(k: K, v: WizardForm[K]) => void
  provider: Catalog['providers'][number] | null
  profile: Catalog['profiles'][number] | null
  osFamilies: OsVariant[]
  productsForSel: Product[]
  selectedProduct: Product | null
  pickProvider: (id: string) => void
  pickStage: (id: string) => void
  pickType: (id: string) => void
  pickOsFamily: (id: string) => void
  selectProduct: (id: string) => void
}) {
  const { t } = useTranslation('server_manager')
  const allowedTypes = profile?.allowed_server_types ?? catalog.server_types.map((st) => st.id)
  const curVariant = selectedProduct?.os_variants.find((v) => v.id === form.os_family_id) ?? null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div className="lx-card" style={card}>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 220px', display: 'flex', flexDirection: 'column', gap: 3 }}>
            <label style={fieldLabel}>{t('wizard.step1.serverName', { defaultValue: 'Server name *' })}</label>
            <input className="lx-input" value={form.name} onChange={(e) => setField('name', e.target.value)} placeholder="srv-app-01" />
          </div>
          <div style={{ flex: '1 1 220px', display: 'flex', flexDirection: 'column', gap: 3 }}>
            <label style={fieldLabel}>{t('wizard.step1.hostname', { defaultValue: 'Hostname' })}</label>
            <input className="lx-input" value={form.hostname} onChange={(e) => setField('hostname', e.target.value)} placeholder="srv-app-01.example.com" />
          </div>
        </div>
      </div>

      {/* Provider */}
      <div className="lx-card" style={card}>
        <label style={fieldLabel}>{t('wizard.step1.provider', { defaultValue: 'Provider *' })}</label>
        <div style={tileRow}>
          {catalog.providers.map((p) => (
            <Tile
              key={p.id}
              state={p.placeholder ? 'disabled' : form.provider_id === p.id ? 'selected' : 'normal'}
              onClick={() => pickProvider(p.id)}
              style={{ minWidth: 110, alignItems: 'center' }}
            >
              <Ico name={p.icon} size={20} />
              <span style={{ fontWeight: 700, textAlign: 'center' }}>{p.label}</span>
              {p.placeholder && <span style={{ fontSize: '0.6rem', fontStyle: 'italic', opacity: 0.7 }}>{t('wizard.step1.soon', { defaultValue: 'soon' })}</span>}
            </Tile>
          ))}
        </div>

        {/* Stage */}
        {provider && provider.stages.length > 0 && (
          <>
            {profile && (
              <div style={{ fontSize: '0.7rem', color: 'var(--lx-text-muted)' }}>
                {t('wizard.step1.profileLabel', { defaultValue: 'Profile:' })} <b style={{ color: 'var(--lx-text)' }}>{profile.label}</b> ({profile.allowed_server_types.join(' / ')})
              </div>
            )}
            <label style={fieldLabel}>{t('wizard.step1.stage', { defaultValue: 'Stage *' })}</label>
            <div style={tileRow}>
              {provider.stages.map((s) => (
                <Tile key={s.id} state={form.environment_id === s.id ? 'selected' : 'normal'} onClick={() => pickStage(s.id)} style={{ minWidth: 96, alignItems: 'center' }}>
                  <Ico name={s.icon} />
                  <span style={{ textAlign: 'center' }}>{s.label}</span>
                </Tile>
              ))}
            </div>
          </>
        )}

        {/* Server type */}
        {form.environment_id && (
          <>
            <label style={fieldLabel}>{t('wizard.step1.serverType', { defaultValue: 'Server type *' })}</label>
            <div style={tileRow}>
              {catalog.server_types.map((t) => {
                const disabled = !allowedTypes.includes(t.id)
                return (
                  <Tile key={t.id} state={disabled ? 'disabled' : form.server_type === t.id ? 'selected' : 'normal'} onClick={() => pickType(t.id)} style={{ minWidth: 110, alignItems: 'center' }}>
                    <span style={{ fontWeight: 700, textAlign: 'center' }}>{t.label || t.id}</span>
                  </Tile>
                )
              })}
            </div>
          </>
        )}

        {/* OS family */}
        {form.environment_id && form.server_type && osFamilies.length > 0 && (
          <>
            <label style={fieldLabel}>{t('wizard.step1.os', { defaultValue: 'Operating system *' })}</label>
            <div style={tileRow}>
              {osFamilies.map((fam) => (
                <Tile key={fam.id} state={form.os_family_id === fam.id ? 'selected' : 'normal'} onClick={() => pickOsFamily(fam.id)} style={{ minWidth: 110, flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Ico name={fam.icon} />
                  <span style={{ fontWeight: 700 }}>{fam.label}</span>
                </Tile>
              ))}
            </div>
          </>
        )}

        {/* Product */}
        {form.environment_id && form.server_type && (
          <>
            <label style={fieldLabel}>{t('wizard.step1.product', { defaultValue: 'Server product *' })}</label>
            {productsForSel.length === 0
              ? <div style={{ fontSize: '0.72rem', fontStyle: 'italic', color: 'var(--lx-text-muted)' }}>{t('wizard.step1.noProducts', { defaultValue: 'No products available for this selection.' })}</div>
              : (
                <div style={tileRow}>
                  {productsForSel.map((p) => (
                    <Tile key={p.id} state={form.product_id === p.id ? 'selected' : 'normal'} onClick={() => selectProduct(p.id)} style={{ minWidth: 150, maxWidth: 230 }}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 700 }}>
                        <Ico name={p.icon} />
                        {p.label}
                      </span>
                      {p.description && <span style={{ fontSize: '0.62rem', opacity: 0.7, fontWeight: 400 }}>{p.description.split('\n')[0].slice(0, 80)}</span>}
                    </Tile>
                  ))}
                </div>
              )}
          </>
        )}

        {/* Service class + OS version */}
        {selectedProduct && selectedProduct.service_classes.length > 0 && (
          <>
            <label style={fieldLabel}>{t('wizard.step1.serviceClass', { defaultValue: 'Service class *' })}</label>
            <div style={tileRow}>
              {selectedProduct.service_classes.map((sc) => (
                <Tile key={sc.id} state={form.service_class_id === sc.id ? 'selected' : 'normal'} onClick={() => setField('service_class_id', sc.id)} style={{ minWidth: 100, alignItems: 'center' }}>
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 700 }}>
                    <Ico name={sc.icon} />
                    {sc.label}
                  </span>
                  {sc.sub_label && <span style={{ fontSize: '0.6rem', opacity: 0.7, fontWeight: 400 }}>{sc.sub_label}</span>}
                </Tile>
              ))}
            </div>
          </>
        )}
        {curVariant && curVariant.os_versions.length > 0 && (
          <>
            <label style={fieldLabel}>{t('wizard.step1.osVersion', { defaultValue: 'OS version *' })}</label>
            <div style={tileRow}>
              {curVariant.os_versions.map((ver) => (
                <Tile key={ver.id} state={form.os_version_id === ver.id ? 'selected' : 'normal'} onClick={() => setField('os_version_id', ver.id)} style={{ minWidth: 110, flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <span>{ver.label}</span>
                  <span style={{ fontSize: '0.58rem', opacity: 0.65 }}>{(ver.lifecycle || '').replace(/_/g, ' ')}</span>
                </Tile>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Profile features */}
      {profile && (
        <div className="lx-card" style={card}>
          <label style={fieldLabel}>{t('wizard.step1.profileFeatures', { defaultValue: 'Profile features — {{name}}', name: profile.label })}</label>
          <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
            {profile.features.map((feat) => {
              const ok = feat.available !== false
              const col = ok ? 'var(--lx-state-up)' : 'var(--lx-state-down)'
              return (
                <span key={feat.id} title={feat.description} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: '0.66rem', padding: '2px 8px', borderRadius: 'var(--lx-radius-sm)', color: col, background: `color-mix(in srgb, ${col} 12%, transparent)` }}>
                  <Ico name={feat.icon} size={14} />
                  {ok ? '✓' : '✕'} {feat.label}
                </span>
              )
            })}
          </div>
        </div>
      )}

      {/* Status / tags / notes */}
      <div className="lx-card" style={card}>
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
          <div style={{ flex: '1 1 160px', display: 'flex', flexDirection: 'column', gap: 3 }}>
            <label style={fieldLabel}>{t('wizard.step1.status', { defaultValue: 'Status' })}</label>
            <select className="lx-select" value={form.status} onChange={(e) => setField('status', e.target.value)}>
              {catalog.statuses.map((s) => (<option key={s.id} value={s.id}>{s.label || s.id}</option>))}
            </select>
          </div>
          <div style={{ flex: '2 1 240px', display: 'flex', flexDirection: 'column', gap: 3 }}>
            <label style={fieldLabel}>{t('wizard.step1.tags', { defaultValue: 'Tags (comma-separated)' })}</label>
            <input className="lx-input" value={form.tags} onChange={(e) => setField('tags', e.target.value)} placeholder="prod, db, critical" />
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <label style={fieldLabel}>{t('wizard.step1.notes', { defaultValue: 'Notes' })}</label>
          <textarea className="lx-textarea" style={{ minHeight: 60, resize: 'vertical' }} value={form.notes} onChange={(e) => setField('notes', e.target.value)} />
        </div>
      </div>
    </div>
  )
}

// ─── Step 2 — Hardware ───────────────────────────────────────────────────────

function filterStorage(opts: StorageOption[], serverType: string, allowed: string[] | null): StorageOption[] {
  let list = opts.filter((o) => o.compatible_types.includes(serverType))
  if (allowed && allowed.length) list = list.filter((o) => allowed.includes(o.id))
  return list
}

function Step2({ catalog, form, setForm, profile, selectedProduct, isPhysical, step }: {
  catalog: Catalog
  form: WizardForm
  setForm: React.Dispatch<React.SetStateAction<WizardForm>>
  profile: Catalog['profiles'][number] | null
  selectedProduct: Product | null
  isPhysical: boolean
  step: number
}) {
  const { t } = useTranslation('server_manager')
  const ramSteps = profile?.ram_steps_gb ?? [32, 64, 128, 256, 512]
  const socketOptions = profile?.socket_options ?? [1, 2, 4]
  const ramManualMax = profile?.ram_manual_max_gb ?? 8192
  const storageManualMax = profile?.storage_manual_max_gb ?? 131072
  const multiStorage = profile?.multi_storage ?? true

  const allowedCpu = selectedProduct?.allowed_cpu_ids ?? null
  const allowedStorage = selectedProduct?.allowed_storage_ids ?? null

  const storageOpts = useMemo(
    () => filterStorage(catalog.hardware.storage_options, form.server_type, allowedStorage),
    [catalog, form.server_type, allowedStorage])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ fontSize: '0.74rem', color: 'var(--lx-text-muted)' }}>
        {t('wizard.step2.product', { defaultValue: 'Product:' })} <b style={{ color: 'var(--lx-text)' }}>{selectedProduct?.label ?? '—'}</b> · {form.server_type}
      </div>

      {isPhysical ? (
        <PhysicalHw
          catalog={catalog} form={form} setForm={setForm}
          allowedCpu={allowedCpu} ramSteps={ramSteps} socketOptions={socketOptions} ramManualMax={ramManualMax}
        />
      ) : (
        <VirtualHw
          form={form} setForm={setForm} selectedProduct={selectedProduct}
          ramSteps={ramSteps} vcpuTiles={profile?.vcpu_tiles ?? [1, 2, 4, 8, 16, 32, 64]} ramManualMax={ramManualMax} step={step}
        />
      )}

      <StorageSection
        form={form} setForm={setForm} storageOptions={storageOpts}
        multiStorage={multiStorage} storageManualMax={storageManualMax}
      />
    </div>
  )
}

function PhysicalHw({ catalog, form, setForm, allowedCpu, ramSteps, socketOptions, ramManualMax }: {
  catalog: Catalog
  form: WizardForm
  setForm: React.Dispatch<React.SetStateAction<WizardForm>>
  allowedCpu: string[] | null
  ramSteps: number[]
  socketOptions: number[]
  ramManualMax: number
}) {
  const { t } = useTranslation('server_manager')
  const cpus = useMemo(() => {
    let list = catalog.hardware.cpu_options.filter((c) => c.compatible_types.includes('physical'))
    if (allowedCpu && allowedCpu.length) list = list.filter((c) => allowedCpu.includes(c.id))
    return list
  }, [catalog, allowedCpu])

  return (
    <>
      <div className="lx-card" style={card}>
        <label style={fieldLabel}>{t('wizard.step2.cpuModel', { defaultValue: 'CPU model *' })}</label>
        <div style={tileRow}>
          {cpus.map((c) => (
            <Tile key={c.id} state={form.cpu_id === c.id ? 'selected' : 'normal'} onClick={() => setForm((f) => ({ ...f, cpu_id: c.id }))} style={{ minWidth: 170 }}>
              <span style={{ fontWeight: 700 }}>{c.label}</span>
              <span style={{ fontSize: '0.6rem', opacity: 0.7, fontWeight: 400 }}>
                {c.cores ? `${c.cores}C/${c.threads ?? c.cores * 2}T` : ''}{c.tdp_w ? ` · ${c.tdp_w}W` : ''}{c.base_ghz ? ` · ${c.base_ghz}GHz` : ''}
              </span>
              {c.server_model && <span style={{ fontSize: '0.58rem', opacity: 0.6, fontStyle: 'italic', fontWeight: 400 }}>{c.server_model}</span>}
            </Tile>
          ))}
          {cpus.length === 0 && <span style={{ fontSize: '0.72rem', fontStyle: 'italic', color: 'var(--lx-text-muted)' }}>{t('wizard.step2.noCpus', { defaultValue: 'No CPUs available.' })}</span>}
        </div>
        <label style={fieldLabel}>{t('wizard.step2.sockets', { defaultValue: 'Sockets' })}</label>
        <div style={tileRow}>
          {socketOptions.map((s) => (
            <Tile key={s} state={form.cpu_count === s ? 'selected' : 'normal'} onClick={() => setForm((f) => ({ ...f, cpu_count: s }))} style={{ minWidth: 48, alignItems: 'center' }}>{s}</Tile>
          ))}
        </div>
      </div>

      <div className="lx-card" style={card}>
        <label style={fieldLabel}>{t('wizard.step2.ramTotal', { defaultValue: 'RAM (GB total) *' })}</label>
        <div style={{ ...tileRow, alignItems: 'center' }}>
          {ramSteps.map((gb) => (
            <Tile key={gb} state={form.ram_gb === gb ? 'selected' : 'normal'} onClick={() => setForm((f) => ({ ...f, ram_gb: gb }))} style={{ minWidth: 56, alignItems: 'center' }}>{gb}</Tile>
          ))}
          <input
            type="number" min={1} max={ramManualMax} step={16}
            className="lx-input" style={{ width: 110 }}
            placeholder={t('wizard.step2.manualGb', { defaultValue: 'manual GB' })}
            value={form.ram_gb ?? ''}
            onChange={(e) => setForm((f) => ({ ...f, ram_gb: e.target.value ? parseInt(e.target.value, 10) : null }))}
          />
        </div>
      </div>
    </>
  )
}

function VirtualHw({ form, setForm, selectedProduct, ramSteps, vcpuTiles, ramManualMax, step }: {
  form: WizardForm
  setForm: React.Dispatch<React.SetStateAction<WizardForm>>
  selectedProduct: Product | null
  ramSteps: number[]
  vcpuTiles: number[]
  ramManualMax: number
  step: number
}) {
  const { t } = useTranslation('server_manager')
  const matrix = selectedProduct?.resources?.vcpu_ram_matrix ?? []
  const hasMatrix = matrix.length > 0
  const ramStep = selectedProduct?.resources?.ram_step_gb ?? 1
  const ramSpecialMax = selectedProduct?.resources?.ram_special_max_gb ?? null
  const osFamily = form.os_family_id || null
  const productId = selectedProduct?.id ?? null

  const vcpuTileValues = useMemo(() => {
    const base = [...vcpuTiles]
    if (!hasMatrix) return base.sort((a, b) => a - b)
    const matrixVals = new Set<number>()
    matrix.forEach((row) => {
      if (row.vcpu != null) matrixVals.add(row.vcpu)
      else if (row.vcpu_min != null && row.vcpu_max != null) { for (let v = row.vcpu_min; v <= row.vcpu_max; v++) matrixVals.add(v) }
    })
    const extras = [...matrixVals].filter((v) => !base.includes(v)).sort((a, b) => a - b).slice(0, 8)
    return [...new Set([...base, ...extras])].sort((a, b) => a - b)
  }, [vcpuTiles, hasMatrix, matrix])

  const ramTileValues = useMemo(() => {
    const vals = new Set(ramSteps)
    if (ramSpecialMax) vals.add(ramSpecialMax)
    return [...vals].sort((a, b) => a - b)
  }, [ramSteps, ramSpecialMax])

  // Authoritative verdicts from the service (debounced) — drive tile greying.
  const [vcpuVerdicts, setVcpuVerdicts] = useState<Record<number, ComboVerdict>>({})
  const [ramVerdicts, setRamVerdicts] = useState<Record<number, ComboVerdict>>({})
  const [comboHint, setComboHint] = useState<ComboVerdict | null>(null)

  useEffect(() => {
    if (step !== 1 || !hasMatrix || !osFamily || !productId) {
      setVcpuVerdicts({}); setRamVerdicts({}); setComboHint(null); return
    }
    let cancel = false
    const t = setTimeout(async () => {
      try {
        // vCPU tiles are greyed only by OS-allowance (does this vCPU appear in the
        // matrix for this OS), NOT by the currently-selected RAM — so pass ram_gb=0,
        // which returns "invalid" only when the vCPU/OS combo is absent from the matrix.
        const vEntries = await Promise.all(vcpuTileValues.map(async (v) =>
          [v, await serverManagerApi.evaluateCombo({ product_id: productId, os_family_id: osFamily, vcpu: v, ram_gb: 0 })] as const))
        const vForR = form.vcpu_count ?? 0
        const rEntries = vForR > 0
          ? await Promise.all(ramTileValues.map(async (r) =>
            [r, await serverManagerApi.evaluateCombo({ product_id: productId, os_family_id: osFamily, vcpu: vForR, ram_gb: r })] as const))
          : []
        let hint: ComboVerdict | null = null
        if (form.vcpu_count && form.ram_gb) {
          hint = await serverManagerApi.evaluateCombo({ product_id: productId, os_family_id: osFamily, vcpu: form.vcpu_count, ram_gb: form.ram_gb })
        }
        if (cancel) return
        setVcpuVerdicts(Object.fromEntries(vEntries))
        setRamVerdicts(Object.fromEntries(rEntries))
        setComboHint(hint)
      } catch { /* ignore */ }
    }, 180)
    return () => { cancel = true; clearTimeout(t) }
  }, [step, hasMatrix, osFamily, productId, form.vcpu_count, form.ram_gb, vcpuTileValues, ramTileValues])

  const vcpuState = (v: number): TileState => {
    if (form.vcpu_count === v) return 'selected'
    if (!hasMatrix) return 'normal'
    const verd = vcpuVerdicts[v]
    if (verd && verd.status === 'invalid') return 'disabled'
    return 'normal'
  }
  const ramState = (gb: number): TileState => {
    if (form.ram_gb === gb) return 'selected'
    if (!hasMatrix || !osFamily || !form.vcpu_count) return 'normal'
    const verd = ramVerdicts[gb]
    if (!verd) return 'normal'
    if (verd.status === 'special') return 'special'
    if (verd.status === 'invalid') return 'disabled'
    if (ramStep > 1 && gb % ramStep !== 0) return 'disabled'
    return 'normal'
  }

  return (
    <>
      {hasMatrix && (
        <div style={{ fontSize: '0.72rem', color: osFamily ? 'var(--lx-text-muted)' : 'var(--lx-state-paused)' }}>
          {osFamily
            ? <>{t('wizard.step2.matrixActive', { defaultValue: 'Matrix active for OS "{{os}}"', os: osFamily })}{ramSpecialMax ? t('wizard.step2.matrixSpecial', { defaultValue: ' · Special size up to {{max}} GB', max: ramSpecialMax }) : ''}{ramStep > 1 ? t('wizard.step2.matrixStep', { defaultValue: ' · RAM step {{step}} GB', step: ramStep }) : ''}</>
            : t('wizard.step2.matrixHint', { defaultValue: 'Select an operating system in step 1 to activate the RAM matrix.' })}
        </div>
      )}

      <div className="lx-card" style={card}>
        <label style={fieldLabel}>{t('wizard.step2.vcpuCount', { defaultValue: 'vCPU count *' })}</label>
        <div style={{ ...tileRow, alignItems: 'center' }}>
          {vcpuTileValues.map((v) => (
            <Tile key={v} state={vcpuState(v)} onClick={() => setForm((f) => ({ ...f, vcpu_count: f.vcpu_count === v ? null : v }))} style={{ minWidth: 48, alignItems: 'center' }}>{v}</Tile>
          ))}
          <input type="number" min={1} max={512} step={1} className="lx-input" style={{ width: 90 }} placeholder="vCPU"
            value={form.vcpu_count ?? ''} onChange={(e) => setForm((f) => ({ ...f, vcpu_count: e.target.value ? parseInt(e.target.value, 10) : null }))} />
        </div>
      </div>

      <div className="lx-card" style={card}>
        <label style={fieldLabel}>{t('wizard.step2.ram', { defaultValue: 'RAM (GB) *' })}</label>
        <div style={{ ...tileRow, alignItems: 'center' }}>
          {ramTileValues.map((gb) => {
            const lbl = gb >= 1000 && gb % 1000 === 0 ? `${gb / 1000} TB` : `${gb} GB`
            return <Tile key={gb} state={ramState(gb)} onClick={() => setForm((f) => ({ ...f, ram_gb: f.ram_gb === gb ? null : gb }))} style={{ minWidth: 56, alignItems: 'center' }}>{lbl}</Tile>
          })}
          <input type="number" min={1} max={ramManualMax} step={ramStep || 1} className="lx-input" style={{ width: 110 }} placeholder={t('wizard.step2.manualGb', { defaultValue: 'manual GB' })}
            value={form.ram_gb ?? ''} onChange={(e) => setForm((f) => ({ ...f, ram_gb: e.target.value ? parseInt(e.target.value, 10) : null }))} />
        </div>
        {comboHint && form.vcpu_count && form.ram_gb && (
          <div style={{
            fontSize: '0.72rem',
            color: comboHint.status === 'ok' ? 'var(--lx-state-up)' : comboHint.status === 'special' ? 'var(--lx-state-paused)' : comboHint.status === 'invalid' ? 'var(--lx-state-down)' : 'var(--lx-text-muted)',
          }}>
            {comboHint.status === 'ok'
              ? `${t('wizard.step2.comboOk', { defaultValue: '✓ matches the matrix' })}${comboHint.ram_min != null ? t('wizard.step2.comboRange', { defaultValue: ' (allowed {{min}}–{{max}} GB)', min: comboHint.ram_min, max: comboHint.ram_max }) : ''}`
              : comboHint.message}
          </div>
        )}
      </div>
    </>
  )
}

// ─── Storage section (physical + virtual) ────────────────────────────────────

function StorageSection({ form, setForm, storageOptions, multiStorage, storageManualMax }: {
  form: WizardForm
  setForm: React.Dispatch<React.SetStateAction<WizardForm>>
  storageOptions: StorageOption[]
  multiStorage: boolean
  storageManualMax: number
}) {
  const { t } = useTranslation('server_manager')
  const disks = form.storage_disks
  const isWindows = (form.os_type || '').toLowerCase() === 'windows' || (form.os_family_id || '').toUpperCase() === 'WIN'
  const setDisks = (next: StorageDisk[]) => setForm((f) => ({ ...f, storage_disks: next }))
  const sharedProduct = disks.find((d) => d.disk_id)?.disk_id || storageOptions[0]?.id || ''
  const rowWord = !multiStorage && isWindows
    ? t('wizard.storage.partition', { defaultValue: 'Partition' })
    : t('wizard.storage.volume', { defaultValue: 'Volume' })

  if (storageOptions.length === 0) {
    return (
      <div className="lx-card" style={card}>
        <label style={fieldLabel}>{t('wizard.storage.label', { defaultValue: 'Storage' })}</label>
        <div style={{ fontSize: '0.72rem', fontStyle: 'italic', color: 'var(--lx-text-muted)' }}>{t('wizard.storage.noProducts', { defaultValue: 'No storage products available for this selection.' })}</div>
      </div>
    )
  }

  function pickShared(id: string) {
    if (disks.length === 0) setDisks([{ disk_id: id, mount: '', size_gb: 0 }])
    else setDisks(disks.map((d) => ({ ...d, disk_id: id })))
  }
  function addLine() {
    const id = multiStorage ? storageOptions[0].id : sharedProduct
    setDisks([...disks, { disk_id: id, mount: '', size_gb: 0 }])
  }
  function updateLine(i: number, patch: Partial<StorageDisk>) {
    setDisks(disks.map((d, j) => (j === i ? { ...d, ...patch } : d)))
  }
  function removeLine(i: number) { setDisks(disks.filter((_, j) => j !== i)) }

  const atLimit = !multiStorage && isWindows && disks.length >= 4

  return (
    <div className="lx-card" style={card}>
      <label style={fieldLabel}>{multiStorage ? t('wizard.storage.multiAllowed', { defaultValue: 'Storage — multiple products allowed' }) : t('wizard.storage.singleProduct', { defaultValue: 'Storage — one product per server' })}</label>

      {!multiStorage && (
        <>
          <span style={{ fontSize: '0.66rem', color: 'var(--lx-text-muted)' }}>{t('wizard.storage.product', { defaultValue: 'Storage product' })}</span>
          <div style={tileRow}>
            {storageOptions.map((sp) => (
              <Tile key={sp.id} state={sharedProduct === sp.id ? 'selected' : 'normal'} onClick={() => pickShared(sp.id)} style={{ minWidth: 150 }}>
                <span style={{ fontWeight: 700 }}>{sp.label}</span>
                {sp.type && <span style={{ fontSize: '0.6rem', opacity: 0.7, fontWeight: 400 }}>{sp.type}</span>}
              </Tile>
            ))}
          </div>
        </>
      )}

      {disks.map((entry, idx) => (
        <div key={idx} style={{ border: '1px solid var(--lx-border-soft)', borderRadius: 'var(--lx-radius-sm)', padding: '0.6rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={fieldLabel}>{rowWord} {idx + 1}</span>
            <button type="button" onClick={() => removeLine(idx)} title={t('wizard.storage.remove', { defaultValue: 'Remove' })} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--lx-state-down)', fontSize: '0.95rem' }}>×</button>
          </div>
          {multiStorage && (
            <div style={tileRow}>
              {storageOptions.map((sp) => (
                <Tile key={sp.id} state={entry.disk_id === sp.id ? 'selected' : 'normal'} onClick={() => updateLine(idx, { disk_id: sp.id })} style={{ minWidth: 140 }}>
                  <span style={{ fontWeight: 700 }}>{sp.label}</span>
                  {sp.type && <span style={{ fontSize: '0.6rem', opacity: 0.7, fontWeight: 400 }}>{sp.type}</span>}
                </Tile>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
            <input
              className="lx-input" style={{ flex: '1 1 160px' }}
              placeholder={isWindows ? t('wizard.storage.mountWindows', { defaultValue: 'C:\\ or D:\\' }) : t('wizard.storage.mountUnix', { defaultValue: '/ or /data' })}
              value={entry.mount}
              onChange={(e) => updateLine(idx, { mount: e.target.value })}
            />
            <input
              type="number" min={1} max={storageManualMax} step={10}
              className="lx-input" style={{ width: 130 }}
              placeholder="GB"
              value={entry.size_gb || ''}
              onChange={(e) => updateLine(idx, { size_gb: e.target.value ? parseInt(e.target.value, 10) : 0 })}
            />
          </div>
        </div>
      ))}

      {atLimit
        ? <span style={{ fontSize: '0.7rem', color: 'var(--lx-state-paused)' }}>{t('wizard.storage.maxPartitions', { defaultValue: 'Maximum of 4 partitions reached.' })}</span>
        : <div><Btn label={!multiStorage && isWindows ? t('wizard.storage.addPartition', { defaultValue: '+ Partition' }) : t('wizard.storage.addStorage', { defaultValue: '+ Storage' })} onClick={addLine} /></div>}
    </div>
  )
}

// ─── Step 3 — Prüfen & Speichern ─────────────────────────────────────────────

function Step3({ catalog, form, issues, isPhysical }: {
  catalog: Catalog
  form: WizardForm
  issues: ValidationIssue[]
  isPhysical: boolean
}) {
  const { t } = useTranslation('server_manager')
  const env = catalog.environments.find((e) => e.id === form.environment_id)
  const profile = catalog.profiles.find((p) => p.id === (env?.profile_id ?? ''))
  const product = catalog.products.find((p) => p.id === form.product_id)
  const sc = product?.service_classes.find((s) => s.id === form.service_class_id)
  const variant = product?.os_variants.find((v) => v.id === form.os_family_id)
  const version = variant?.os_versions.find((v) => v.id === form.os_version_id)
  const cpu = catalog.hardware.cpu_options.find((c) => c.id === form.cpu_id)

  const rows: [string, string][] = [
    [t('wizard.step3.rowName', { defaultValue: 'Name' }), form.name],
    [t('wizard.step3.rowHostname', { defaultValue: 'Hostname' }), form.hostname || '—'],
    [t('wizard.step3.rowEnvironment', { defaultValue: 'Environment' }), env ? (env.provider_label ? `${env.provider_label} · ${env.label}` : env.label) : form.environment_id],
    [t('wizard.step3.rowProfile', { defaultValue: 'Profile' }), profile?.label ?? '—'],
    [t('wizard.step3.rowProduct', { defaultValue: 'Product' }), product?.label ?? '—'],
  ]
  if (sc) rows.push([t('wizard.step3.rowServiceClass', { defaultValue: 'Service class' }), `${sc.label}${sc.sub_label ? ` — ${sc.sub_label}` : ''}`])
  if (variant) rows.push([t('wizard.step3.rowOsFamily', { defaultValue: 'OS family' }), variant.label])
  if (version) rows.push([t('wizard.step3.rowOsVersion', { defaultValue: 'OS version' }), `${version.label} — ${(version.lifecycle || '').replace(/_/g, ' ')}`])
  rows.push([t('wizard.step3.rowType', { defaultValue: 'Type' }), form.server_type], [t('wizard.step3.rowStatus', { defaultValue: 'Status' }), form.status], [t('wizard.step3.rowTags', { defaultValue: 'Tags' }), form.tags || '—'])
  if (isPhysical) {
    rows.push([t('wizard.step3.rowCpu', { defaultValue: 'CPU' }), cpu ? `${form.cpu_count}× ${cpu.label}` : '—'])
    rows.push([t('wizard.step3.rowRam', { defaultValue: 'RAM' }), form.ram_gb ? `${form.ram_gb} GB` : '—'])
  } else {
    rows.push([t('wizard.step3.rowVcpu', { defaultValue: 'vCPU' }), form.vcpu_count ? String(form.vcpu_count) : '—'])
    rows.push([t('wizard.step3.rowRam', { defaultValue: 'RAM' }), form.ram_gb ? `${form.ram_gb} GB` : '—'])
  }

  const totalGb = form.storage_disks.reduce((sum, d) => sum + (d.size_gb || 0), 0)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div className="lx-card" style={card}>
        {rows.map(([k, v]) => (
          <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem' }}>
            <span style={{ ...fieldLabel, width: 140, flexShrink: 0 }}>{k}</span>
            <span style={{ fontSize: '0.78rem', color: 'var(--lx-text)' }}>{v}</span>
          </div>
        ))}
        {form.storage_disks.length > 0 && (
          <>
            <div style={{ height: 1, background: 'var(--lx-border-soft)' }} />
            <label style={fieldLabel}>{t('wizard.step3.storage', { defaultValue: 'Storage' })}</label>
            {form.storage_disks.map((d, i) => {
              const item = catalog.hardware.storage_options.find((s) => s.id === d.disk_id)
              const szStr = d.size_gb >= 1024 ? `${(d.size_gb / 1024).toFixed(1)} TB` : `${d.size_gb} GB`
              return (
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', paddingLeft: '0.5rem' }}>
                  <span style={{ fontSize: '0.72rem', fontFamily: 'var(--lx-font-mono)', color: 'var(--lx-text-muted)', width: 140, flexShrink: 0 }}>{d.mount || '—'}</span>
                  <span style={{ fontSize: '0.72rem', color: 'var(--lx-text-muted)' }}>{d.size_gb ? szStr : '?'} [{item?.label ?? d.disk_id}]</span>
                </div>
              )
            })}
            {totalGb > 0 && (
              <span style={{ fontSize: '0.74rem', fontWeight: 600, color: 'var(--lx-state-up)', paddingLeft: '0.5rem' }}>
                {t('wizard.step3.total', { defaultValue: 'Total: {{size}}', size: totalGb >= 1024 ? `${(totalGb / 1024).toFixed(1)} TB` : `${totalGb} GB` })}
              </span>
            )}
          </>
        )}
      </div>

      {/* Validation panel */}
      <div className="lx-card" style={card}>
        <label style={fieldLabel}>{t('wizard.step3.ruleCheck', { defaultValue: 'Rule check' })}</label>
        {issues.length === 0
          ? <span style={{ fontSize: '0.76rem', color: 'var(--lx-state-up)' }}>{t('wizard.step3.rulesOk', { defaultValue: '✓ All rules satisfied.' })}</span>
          : issues.map((iss, i) => {
            const isErr = iss.severity === 'error'
            const col = isErr ? 'var(--lx-state-down)' : 'var(--lx-state-paused)'
            return (
              <div key={i} style={{ display: 'flex', gap: 6, fontSize: '0.74rem', color: col }}>
                <span>{isErr ? '✕' : '⚠'}</span>
                <span>{iss.message}</span>
              </div>
            )
          })}
        {issues.some((i) => i.severity === 'error') && (
          <span style={{ fontSize: '0.7rem', color: 'var(--lx-text-muted)' }}>{t('wizard.step3.fixErrors', { defaultValue: 'Please fix all errors before saving.' })}</span>
        )}
      </div>
    </div>
  )
}
