import { useCallback, useEffect, useState } from 'react'
import { Check, KeyRound, LoaderCircle, Plus, Server, ShieldCheck, Trash2, X } from 'lucide-react'

export type ProviderPreset = {
  id: string
  name: string
  protocol: 'openai' | 'gemini' | 'anthropic'
  base_url: string
}

type Model = {
  id: string
  name: string
  provider: string
  is_default?: boolean
}

type ConfiguredProvider = {
  id: string
  provider_id: string
  provider: string
  name: string
  protocol: ProviderPreset['protocol']
  base_url: string
  model_id: string
  organization: string
  enabled: boolean
  key_saved: boolean
  status: string
  status_message: string
  managed_by_server?: boolean
}

type Props = {
  open: boolean
  accessToken: string
  initialProviderId?: string
  onClose: () => void
  onModelsChanged: () => void
}

type ResponseError = { detail?: string }
type ConfiguredProvidersResponse = {
  providers: ConfiguredProvider[]
  credential_storage_available?: boolean
}

async function responseError(response: Response) {
  try {
    const body = (await response.json()) as ResponseError
    return body.detail ?? `Request failed (HTTP ${response.status}).`
  } catch {
    return `Request failed (HTTP ${response.status}).`
  }
}

export default function ProvidersSettings({
  open,
  accessToken,
  initialProviderId,
  onClose,
  onModelsChanged,
}: Props) {
  const [presets, setPresets] = useState<ProviderPreset[]>([])
  const [providers, setProviders] = useState<ConfiguredProvider[]>([])
  const [credentialStorageAvailable, setCredentialStorageAvailable] = useState<boolean | null>(null)
  const [models, setModels] = useState<Model[]>([])
  const [defaultModel, setDefaultModel] = useState('demo')
  const [presetId, setPresetId] = useState('ollama')
  const [editingId, setEditingId] = useState('')
  const [displayName, setDisplayName] = useState('Ollama')
  const [protocol, setProtocol] = useState<ProviderPreset['protocol']>('openai')
  const [baseUrl, setBaseUrl] = useState('http://127.0.0.1:11434/v1')
  const [modelId, setModelId] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [organization, setOrganization] = useState('')
  const [discoveredModels, setDiscoveredModels] = useState<{ id: string; name: string }[]>([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')

  const headers = useCallback(() => ({
    Authorization: `Bearer ${accessToken}`,
    'Content-Type': 'application/json',
  }), [accessToken])

  const loadProviders = async () => {
    const response = await fetch('/api/v1/providers', { headers: headers() })
    if (!response.ok) throw new Error(await responseError(response))
    const payload = (await response.json()) as ConfiguredProvidersResponse
    setProviders(payload.providers)
    setCredentialStorageAvailable(payload.credential_storage_available ?? true)
  }

  useEffect(() => {
    if (!open) return
    let active = true
    const load = async () => {
      try {
        const [catalogResponse, modelsResponse] = await Promise.all([
          fetch('/api/v1/providers/catalog', { headers: headers() }),
          fetch('/api/v1/models', { headers: headers() }),
        ])
        if (!catalogResponse.ok) throw new Error(await responseError(catalogResponse))
        if (!modelsResponse.ok) throw new Error(await responseError(modelsResponse))
        const catalog = (await catalogResponse.json()) as { providers: ProviderPreset[] }
        const modelList = (await modelsResponse.json()) as { models: Model[] }
        if (!active) return
        setPresets(catalog.providers)
        const requestedPreset = catalog.providers.find((preset) => preset.id === initialProviderId)
        if (requestedPreset) {
          setPresetId(requestedPreset.id)
          setDisplayName(requestedPreset.name)
          setProtocol(requestedPreset.protocol)
          setBaseUrl(requestedPreset.base_url)
          setModelId('')
          setApiKey('')
          setOrganization('')
          setEditingId('')
          setDiscoveredModels([])
        }
        setModels(modelList.models)
        setDefaultModel(modelList.models.find((model) => model.is_default)?.id ?? modelList.models[0]?.id ?? 'demo')
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : 'Could not load provider settings.')
      }
    }
    void load()
    return () => { active = false }
  }, [open, headers, initialProviderId])

  useEffect(() => {
    if (!open) return
    let active = true
    const load = async () => {
      try {
        const response = await fetch('/api/v1/providers', {
          headers: { Authorization: `Bearer ${accessToken}` },
        })
        if (!response.ok) throw new Error(await responseError(response))
        const payload = (await response.json()) as ConfiguredProvidersResponse
        if (active) {
          setProviders(payload.providers)
          setCredentialStorageAvailable(payload.credential_storage_available ?? true)
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : 'Could not unlock provider settings.')
      }
    }
    void load()
    return () => { active = false }
  }, [open, accessToken])

  const updatePreset = (id: string) => {
    setPresetId(id)
    setEditingId('')
    setModelId('')
    setApiKey('')
    setOrganization('')
    setDiscoveredModels([])
    const preset = presets.find((item) => item.id === id)
    if (!preset) return
    setDisplayName(preset.name)
    setProtocol(preset.protocol)
    setBaseUrl(preset.base_url)
  }

  const loadModels = async () => {
    const response = await fetch('/api/v1/models', { headers: headers() })
    if (!response.ok) throw new Error(await responseError(response))
    const payload = (await response.json()) as { models: Model[] }
    setModels(payload.models)
    setDefaultModel(payload.models.find((model) => model.is_default)?.id ?? payload.models[0]?.id ?? 'demo')
  }

  const refresh = async () => {
    await Promise.all([loadProviders(), loadModels()])
    onModelsChanged()
  }

  const saveProvider = async () => {
    setError('')
    setNotice('')
    if (!displayName.trim() || !modelId.trim()) {
      setError('Enter a provider name and exact model ID.')
      return
    }
    setBusy('save')
    try {
      const response = await fetch('/api/v1/providers', {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify({
          id: editingId || undefined,
          provider_id: presetId,
          display_name: displayName,
          protocol,
          base_url: baseUrl,
          model_id: modelId,
          api_key: apiKey,
          organization,
          enabled: true,
        }),
      })
      if (!response.ok) throw new Error(await responseError(response))
      const saved = (await response.json()) as { id: string }
      setEditingId(saved.id)
      setApiKey('')
      setNotice('Provider settings saved encrypted on the server. Test the connection before use.')
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save provider settings.')
    } finally {
      setBusy('')
    }
  }

  const discover = async (provider: ConfiguredProvider) => {
    setError('')
    setBusy(`discover:${provider.id}`)
    try {
      const response = await fetch(`/api/v1/providers/${encodeURIComponent(provider.id)}/models`, {
        headers: headers(),
      })
      if (!response.ok) throw new Error(await responseError(response))
      const payload = (await response.json()) as { models: { id: string; name: string }[] }
      setDiscoveredModels(payload.models)
      setEditingId(provider.id)
      setPresetId(provider.provider_id)
      setDisplayName(provider.name)
      setProtocol(provider.protocol)
      setBaseUrl(provider.base_url)
      setModelId(provider.model_id)
      setOrganization(provider.organization)
      setApiKey('')
      setNotice(`Discovered ${payload.models.length} models. Select the exact model ID below.`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not discover models.')
    } finally {
      setBusy('')
    }
  }

  const testConnection = async (provider: ConfiguredProvider) => {
    setError('')
    setBusy(`test:${provider.id}`)
    try {
      const response = await fetch(`/api/v1/providers/${encodeURIComponent(provider.id)}/test`, {
        method: 'POST',
        headers: headers(),
      })
      if (!response.ok) throw new Error(await responseError(response))
      setNotice(`${provider.name} verified the configured model.`)
      if (provider.managed_by_server) {
        setProviders((current) => current.map((item) =>
          item.id === provider.id ? { ...item, status: 'connected', status_message: '' } : item,
        ))
      } else {
        await refresh()
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Provider connection test failed.')
      await loadProviders()
    } finally {
      setBusy('')
    }
  }

  const makeDefault = async (model: Model) => {
    setError('')
    setBusy(`default:${model.id}`)
    try {
      const response = await fetch('/api/v1/default-model', {
        method: 'PUT',
        headers: headers(),
        body: JSON.stringify({ model_id: model.id }),
      })
      if (!response.ok) throw new Error(await responseError(response))
      setNotice(`${model.provider} · ${model.name} is now the default model.`)
      setDefaultModel(model.id)
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not set the default model.')
    } finally {
      setBusy('')
    }
  }

  const deleteProvider = async (provider: ConfiguredProvider) => {
    if (!window.confirm(`Delete the saved ${provider.name} credential and settings?`)) return
    setError('')
    setBusy(`delete:${provider.id}`)
    try {
      const response = await fetch(`/api/v1/providers/${encodeURIComponent(provider.id)}`, {
        method: 'DELETE',
        headers: headers(),
      })
      if (!response.ok) throw new Error(await responseError(response))
      if (editingId === provider.id) resetForm()
      setNotice('Saved provider credential deleted.')
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not delete the provider.')
    } finally {
      setBusy('')
    }
  }

  const toggleProvider = async (provider: ConfiguredProvider) => {
    setError('')
    setBusy(`toggle:${provider.id}`)
    try {
      const response = await fetch('/api/v1/providers', {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify({
          id: provider.id,
          provider_id: provider.provider_id,
          display_name: provider.name,
          protocol: provider.protocol,
          base_url: provider.base_url,
          model_id: provider.model_id,
          api_key: '',
          organization: provider.organization,
          enabled: !provider.enabled,
        }),
      })
      if (!response.ok) throw new Error(await responseError(response))
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not update the provider.')
    } finally {
      setBusy('')
    }
  }

  const resetForm = () => {
    setEditingId('')
    updatePreset('ollama')
  }

  if (!open) return null

  return (
    <div className="providers-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose()
    }}>
      <section className="providers-dialog" role="dialog" aria-modal="true" aria-labelledby="providers-title">
        <header className="providers-header">
          <div className="providers-heading-icon"><Server size={19} /></div>
          <div>
            <span className="settings-eyebrow">YOUR ACCOUNT · SERVER-SIDE KEYS</span>
            <h2 id="providers-title">AI Providers</h2>
          </div>
          <button className="icon-button settings-close" aria-label="Close settings" onClick={onClose}><X size={18} /></button>
        </header>

        <p className="providers-intro">
          {accessToken
            ? 'Provider keys are encrypted by the backend and accessible only to your signed-in account.'
            : 'Local guest mode can use the provider key saved on this computer. It is not available on the public deployment.'}
        </p>
        {credentialStorageAvailable === false && (
          <div className="auth-setup" role="status">
            <strong>Personal provider storage needs setup</strong>
            <span>Server Gemini can still be used. To save other API keys, generate a Fernet key and set <code>CREDENTIAL_ENCRYPTION_KEY</code> in the backend settings, then restart FastAPI.</span>
          </div>
        )}

        <>
            <div className="settings-section-title"><ShieldCheck size={15} /> Saved providers</div>
            {providers.length === 0 ? (
              <p className="settings-empty">No AI providers are configured for this account yet.</p>
            ) : (
              <div className="provider-list">
                {providers.map((provider) => (
                  <article className="provider-card" key={provider.id}>
                    <div className="provider-card-main">
                      <strong>{provider.name}</strong>
                      <span>{provider.provider} · {provider.model_id}</span>
                      <small className={provider.status === 'connected' ? 'provider-status verified' : 'provider-status'}>
                        {provider.enabled ? provider.status.replaceAll('_', ' ') : 'disabled'}
                        {provider.key_saved ? ' · key saved (hidden)' : ' · no key required'}
                      </small>
                      {provider.status_message && <small className="provider-status-message">{provider.status_message}</small>}
                    </div>
                    <div className="provider-card-actions">
                      {!provider.managed_by_server && (
                        <button className="settings-small-button" disabled={Boolean(busy)} onClick={() => void discover(provider)}>
                          {busy === `discover:${provider.id}` ? <LoaderCircle size={13} className="spin" /> : 'Models'}
                        </button>
                      )}
                      <button className="settings-small-button" disabled={Boolean(busy) || !provider.enabled} onClick={() => void testConnection(provider)}>
                        {busy === `test:${provider.id}` ? <LoaderCircle size={13} className="spin" /> : 'Test'}
                      </button>
                      {!provider.managed_by_server && (
                        <>
                          <button className="settings-icon-button" title={provider.enabled ? 'Disable provider' : 'Enable provider'} disabled={Boolean(busy)} onClick={() => void toggleProvider(provider)}>
                            {provider.enabled ? <Check size={14} /> : <Plus size={14} />}
                          </button>
                          <button className="settings-icon-button danger" title="Delete saved provider" disabled={Boolean(busy)} onClick={() => void deleteProvider(provider)}><Trash2 size={14} /></button>
                        </>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}

            <div className="settings-section-title"><KeyRound size={15} /> Add or edit a provider</div>
            <div className="settings-form-grid">
              <label className="settings-field">
                <span>Provider</span>
                <select value={presetId} onChange={(event) => updatePreset(event.target.value)}>
                  {presets.map((preset) => <option value={preset.id} key={preset.id}>{preset.name}</option>)}
                </select>
              </label>
              <label className="settings-field">
                <span>Display name</span>
                <input value={displayName} maxLength={100} onChange={(event) => setDisplayName(event.target.value)} />
              </label>
              {presetId === 'custom' && (
                <label className="settings-field">
                  <span>API protocol</span>
                  <select value={protocol} onChange={(event) => setProtocol(event.target.value as ProviderPreset['protocol'])}>
                    <option value="openai">OpenAI-compatible</option>
                    <option value="anthropic">Anthropic Messages</option>
                    <option value="gemini">Google Gemini</option>
                  </select>
                </label>
              )}
              <label className="settings-field settings-field-wide">
                <span>API base URL / Ollama HTTPS endpoint</span>
                <input value={baseUrl} maxLength={2000} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://your-provider.example/v1" />
                <small>Hosted Ollama: use its secure HTTPS URL. localhost on Render refers to Render, not your PC.</small>
              </label>
              <label className="settings-field">
                <span>Exact model ID</span>
                <input value={modelId} maxLength={256} onChange={(event) => setModelId(event.target.value)} placeholder="e.g. llama3.1:8b" />
              </label>
              <label className="settings-field">
                <span>Organization / project ID (optional)</span>
                <input value={organization} maxLength={200} onChange={(event) => setOrganization(event.target.value)} />
              </label>
              <label className="settings-field settings-field-wide">
                <span>API key {editingId ? '(leave empty to keep the saved key)' : '(optional for Ollama)'}</span>
                <input type="password" autoComplete="new-password" value={apiKey} maxLength={4000} onChange={(event) => setApiKey(event.target.value)} placeholder={editingId ? 'Saved key is hidden; enter a replacement only' : 'Enter key directly here; never paste it into chat'} />
                <small>Local Ollama needs no key; hosted providers may require your API key.</small>
              </label>
            </div>
            {discoveredModels.length > 0 && (
              <label className="settings-field discovered-models">
                <span>Discovered models</span>
                <select value={modelId} onChange={(event) => setModelId(event.target.value)}>
                  <option value="">Choose exact model ID</option>
                  {discoveredModels.map((model) => <option key={model.id} value={model.id}>{model.id}</option>)}
                </select>
              </label>
            )}
            <div className="provider-form-actions">
              <button className="settings-primary-button" disabled={Boolean(busy) || credentialStorageAvailable === false} onClick={() => void saveProvider()}>
                {busy === 'save' ? <LoaderCircle size={15} className="spin" /> : <><Plus size={15} /> Save encrypted provider</>}
              </button>
              {editingId && <button className="settings-small-button" onClick={resetForm}>Clear form</button>}
            </div>

            <div className="settings-section-title"><Check size={15} /> Default model</div>
            <div className="default-model-row">
              <select aria-label="Default model" value={defaultModel} onChange={(event) => setDefaultModel(event.target.value)}>
                {models.map((model) => <option value={model.id} key={model.id}>{model.provider} · {model.name}</option>)}
              </select>
              <button className="settings-small-button" disabled={Boolean(busy) || credentialStorageAvailable === false} onClick={() => {
                const model = models.find((item) => item.id === defaultModel)
                if (model) void makeDefault(model)
              }}>{busy.startsWith('default:') ? 'Saving…' : 'Set default'}</button>
            </div>
        </>

        {error && <div className="settings-message settings-error" role="alert">{error}</div>}
        {notice && <div className="settings-message" role="status">{notice}</div>}

        <footer className="providers-footer">
          <span><ShieldCheck size={14} /> Never store provider keys in browser storage or chat.</span>
          <button className="settings-small-button" onClick={onClose}>Done</button>
        </footer>
      </section>
    </div>
  )
}
