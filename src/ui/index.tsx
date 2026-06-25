import PluginApp from './PluginApp'

// Explicit global assignment — more reliable than relying on Rollup's IIFE
// named-export → window property mechanism in strict-mode bundles.
;(window as unknown as Record<string, unknown>)['__lyndrix_plugin_lyndrix_plugin_server_manager'] = {
  PluginApp,
  pluginRoutes: [
    { path: '/server-manager', label: 'Server Manager', icon: 'dns', sidebar_visible: true },
  ],
}
