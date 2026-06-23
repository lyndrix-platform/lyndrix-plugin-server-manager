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
  status?: string | null
  tags?: string[]
  notes?: string | null
  os_type?: string | null
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

export interface Catalog {
  catalog_dir: string
  is_default: boolean
  environments: EnvironmentOption[]
  server_types: CatalogOption[]
  statuses: StatusOption[]
  os_types: CatalogOption[]
  profiles: CatalogOption[]
  products: CatalogOption[]
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
}
