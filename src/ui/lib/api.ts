const PLUGIN_ID_PREFIX = 'lyndrix.plugin.server_manager'
const TOKEN_KEY = 'lyndrix_token'

// ─── Types ──────────────────────────────────────────────────────────────────

export interface ServerRecord {
  id: number
  name: string
  hostname: string | null
  environment_id: string
  server_type: string
  product_id: string | null
  service_class_id: string | null
  os_family_id: string | null
  os_version_id: string | null
  os_type: string | null
  status: string
  hardware_profile: Record<string, unknown>
  tags: string[]
  notes: string | null
  ordered_by: string | null
  ordered_at: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ServerInput {
  id?: number
  name: string
  hostname?: string | null
  environment_id: string
  server_type: string
  product_id?: string | null
  service_class_id?: string | null
  os_type?: string | null
  os_family_id?: string | null
  os_version_id?: string | null
  status?: string | null
  tags?: string[]
  notes?: string | null
  hardware_profile?: Record<string, unknown>
}

export interface CatalogOption {
  id: string
  label: string
  [key: string]: unknown
}

export interface EnvironmentOption {
  id: string
  label: string
  provider_id: string | null
  provider_label: string | null
  profile_id: string
}

export interface StatusOption {
  id: string
  label?: string
  color?: string
  terminal?: boolean
}

// ─── Wizard catalog shapes ──────────────────────────────────────────────────

export interface ProfileFeature {
  id: string
  label: string
  icon?: string
  available?: boolean
  description?: string
}

export interface Profile {
  id: string
  label: string
  icon?: string | null
  color?: string | null
  allowed_server_types: string[]
  features: ProfileFeature[]
  ram_steps_gb: number[]
  vcpu_tiles: number[]
  socket_options: number[]
  ram_manual_max_gb: number
  storage_manual_max_gb: number
  multi_storage: boolean
}

export interface OsVersion {
  id: string
  label: string
  lifecycle?: string
  end_of_support?: string
}

export interface OsVariant {
  id: string
  os_type?: string
  label: string
  icon?: string
  os_versions: OsVersion[]
}

export interface ServiceClass {
  id: string
  label: string
  sub_label?: string
  description?: string
  icon?: string
  color?: string
}

export interface MatrixRow {
  vcpu?: number
  vcpu_min?: number
  vcpu_max?: number
  generation?: string
  per_os?: Record<string, { ram_min: number; ram_max: number }>
}

export interface ProductResources {
  form_factor?: string
  ram_step_gb?: number
  ram_special_max_gb?: number | null
  vcpu_ram_matrix?: MatrixRow[]
  physical_resources?: Record<string, unknown>
}

export interface Product {
  id: string
  label: string
  icon?: string | null
  description?: string | null
  compatible_profiles: string[]
  compatible_server_types: string[]
  service_classes: ServiceClass[]
  os_variants: OsVariant[]
  allowed_cpu_ids: string[] | null
  allowed_storage_ids: string[] | null
  resources: ProductResources
}

export interface CpuOption {
  id: string
  label: string
  cores?: number | null
  threads?: number | null
  tdp_w?: number | null
  base_ghz?: number | null
  server_model?: string | null
  compatible_types: string[]
}

export interface StorageOption {
  id: string
  label: string
  type?: string | null
  description?: string | null
  compatible_types: string[]
}

export interface RamOption {
  id: string
  label: string
  size_gb?: number | null
  compatible_types: string[]
}

export interface Hardware {
  cpu_options: CpuOption[]
  storage_options: StorageOption[]
  ram_options: RamOption[]
}

export interface ProviderStage {
  id: string
  label: string
  icon?: string | null
  color?: string | null
  order_workflow?: string | null
  description?: string | null
}

export interface Provider {
  id: string
  label: string
  profile_id: string
  icon?: string | null
  color?: string | null
  placeholder: boolean
  description?: string | null
  stages: ProviderStage[]
}

export interface ComboVerdict {
  status: 'ok' | 'special' | 'invalid' | 'unknown'
  message: string
  ram_min: number | null
  ram_max: number | null
  generation?: string | null
  alt_platform?: boolean
}

export interface ValidationIssue {
  rule_id: string
  severity: 'error' | 'warning'
  message: string
}

export interface Catalog {
  catalog_dir: string
  is_default: boolean
  environments: EnvironmentOption[]
  providers: Provider[]
  server_types: CatalogOption[]
  statuses: StatusOption[]
  os_types: CatalogOption[]
  profiles: Profile[]
  products: Product[]
  hardware: Hardware
  stats: Record<string, number>
}

// ─── Fetch wrapper ──────────────────────────────────────────────────────────

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY)
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(init.headers as Record<string, string> | undefined),
  }

  const res = await fetch(path, { ...init, headers })

  if (res.status === 401) {
    localStorage.removeItem(TOKEN_KEY)
    window.location.href = '/login'
    throw new Error('Nicht autorisiert')
  }

  if (!res.ok) {
    let msg = `HTTP ${res.status}`
    try {
      const body = (await res.json()) as { detail?: string }
      msg = body.detail ?? msg
    } catch { /* ignore */ }
    throw new Error(msg)
  }

  return res.json() as Promise<T>
}

export const pluginApi = {
  get: <T>(subpath: string) =>
    apiFetch<T>(`/api/plugins/${PLUGIN_ID_PREFIX}/${subpath}`),

  post: <T>(subpath: string, body?: unknown) =>
    apiFetch<T>(`/api/plugins/${PLUGIN_ID_PREFIX}/${subpath}`, {
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),

  patch: <T>(subpath: string, body?: unknown) =>
    apiFetch<T>(`/api/plugins/${PLUGIN_ID_PREFIX}/${subpath}`, {
      method: 'PATCH',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),

  del: <T>(subpath: string) =>
    apiFetch<T>(`/api/plugins/${PLUGIN_ID_PREFIX}/${subpath}`, { method: 'DELETE' }),
}

// ─── Server Manager surface ─────────────────────────────────────────────────

export const serverManagerApi = {
  listServers: () => pluginApi.get<{ servers: ServerRecord[] }>('servers'),

  getServer: (id: number) => pluginApi.get<{ server: ServerRecord }>(`servers/${id}`),

  createServer: (data: ServerInput) =>
    pluginApi.post<{ server: ServerRecord }>('servers', data),

  updateServer: (id: number, data: Partial<ServerInput>) =>
    pluginApi.patch<{ server: ServerRecord }>(`servers/${id}`, data),

  deleteServer: (id: number) => pluginApi.del<{ ok: boolean }>(`servers/${id}`),

  getCatalog: () => pluginApi.get<Catalog>('catalog'),

  reloadCatalog: () => pluginApi.post<Catalog>('catalog/reload'),

  setCatalogPath: (path: string) => pluginApi.post<Catalog>('catalog/path', { path }),

  evaluateCombo: (body: {
    product_id: string
    os_family_id: string | null
    vcpu: number
    ram_gb: number
  }) => pluginApi.post<ComboVerdict>('catalog/evaluate-combo', body),

  validateServer: (body: {
    server_type: string
    profile: Record<string, unknown>
    profile_id?: string | null
    product_id?: string | null
    environment_id?: string | null
    os_type?: string | null
    os_family_id?: string | null
  }) => pluginApi.post<{ issues: ValidationIssue[] }>('validate', body),
}
