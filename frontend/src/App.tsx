import { AnimatePresence, motion } from 'framer-motion'
import {
  ArrowUp,
  AudioLines,
  Check,
  ChevronDown,
  Code2,
  Cog,
  Copy,
  Database,
  Eye,
  EyeOff,
  FileText,
  Globe2,
  Library,
  LogOut,
  Menu,
  MessageSquareText,
  MoreHorizontal,
  Pin,
  Plus,
  Search,
  Sparkles,
  Square,
  SquarePen,
  ThumbsDown,
  ThumbsUp,
  X,
  Volume2,
} from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useRef, useState, useSyncExternalStore, type ChangeEvent } from 'react'
import remarkGfm from 'remark-gfm'
import ProvidersSettings, { type ProviderPreset } from './ProvidersSettings'
import IncidentsSettings, { type IncidentFile } from './IncidentsSettings'
import { useAuth } from './useAuth'
import './App.css'

const ReactMarkdown = lazy(() => import('react-markdown'))

type Role = 'user' | 'assistant'

type ChatMessage = {
  id: string
  role: Role
  content: string
  screenActions?: ScreenAction[]
}

type ScreenAction =
  | { type: 'fill_draft'; text: string }
  | { type: 'new_chat' }

type IncidentUiState = {
  ownerId: string
  files: IncidentFile[]
  mode: boolean
  open: boolean
}

type ScreenShareState = {
  ownerId: string
  stream: MediaStream
}

type Conversation = {
  id: string
  title: string
  updatedAt: number
  model: string
  pinned: boolean
  messages: ChatMessage[]
}

type Model = {
  id: string
  name: string
  provider: string
  configured: boolean
  provider_id?: string
  model_id?: string
  status?: string
  is_default?: boolean
  provider_type?: string
}

type StreamEvent = {
  type: 'token' | 'done' | 'error'
  text: string
}

type SpeechRecognitionResultEvent = Event & {
  resultIndex: number
  results: ArrayLike<ArrayLike<{ transcript: string }>>
}

type SpeechRecognitionErrorEvent = Event & { error: string }

type BrowserSpeechRecognition = {
  lang: string
  interimResults: boolean
  onresult: ((event: SpeechRecognitionResultEvent) => void) | null
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
}

type SpeechRecognitionConstructor = new () => BrowserSpeechRecognition

type SpeechRecognitionWindow = Window & {
  SpeechRecognition?: SpeechRecognitionConstructor
  webkitSpeechRecognition?: SpeechRecognitionConstructor
}

const STORAGE_KEY_PREFIX = 'ai-ans.conversations.v2.'
let storageUnavailable = false
const storageSubscribers = new Set<() => void>()

function subscribeToStorageStatus(subscriber: () => void) {
  storageSubscribers.add(subscriber)
  return () => storageSubscribers.delete(subscriber)
}

function getStorageStatus() {
  return storageUnavailable
}

function reportStorageStatus(unavailable: boolean) {
  if (storageUnavailable === unavailable) return
  storageUnavailable = unavailable
  storageSubscribers.forEach((subscriber) => subscriber())
}

function currentTimestamp() {
  return Date.now()
}

const SUGGESTIONS = [
  { icon: Code2, label: 'Write some code', prompt: 'Help me write a simple Python program that ' },
  { icon: Globe2, label: 'Explore an idea', prompt: 'Explain this idea in a clear, beginner-friendly way: ' },
  { icon: FileText, label: 'Summarize text', prompt: 'Help me summarize this: ' },
  { icon: AudioLines, label: 'Plan something', prompt: 'Help me make a practical plan for ' },
]

function readConversations(userId: string): Conversation[] {
  const saved = window.localStorage.getItem(`${STORAGE_KEY_PREFIX}${userId}`)
  if (!saved) return []
  const parsed: unknown = JSON.parse(saved)
  if (!Array.isArray(parsed)) return []
  return parsed.filter(
    (item): item is Conversation =>
      typeof item === 'object' &&
      item !== null &&
      typeof item.id === 'string' &&
      typeof item.title === 'string' &&
      Array.isArray(item.messages),
  )
}

function createConversation(model: string): Conversation {
  return {
    id: crypto.randomUUID(),
    title: 'New conversation',
    updatedAt: Date.now(),
    model,
    pinned: false,
    messages: [],
  }
}

function extractScreenAction(content: string): { content: string; action?: ScreenAction } {
  const match = content.match(/\[\[AI_ANS_ACTION\]\]([\s\S]*?)\[\[\/AI_ANS_ACTION\]\]/)
  if (!match) return { content }
  try {
    const parsed: unknown = JSON.parse(match[1])
    if (typeof parsed !== 'object' || parsed === null || !('type' in parsed)) {
      return { content }
    }
    if (
      parsed.type === 'fill_draft' &&
      'text' in parsed &&
      typeof parsed.text === 'string' &&
      parsed.text.trim().length > 0 &&
      parsed.text.length <= 1_000
    ) {
      return {
        content: content.replace(match[0], '').trimEnd(),
        action: { type: 'fill_draft', text: parsed.text },
      }
    }
    if (parsed.type === 'new_chat') {
      return {
        content: content.replace(match[0], '').trimEnd(),
        action: { type: 'new_chat' },
      }
    }
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error
  }
  return { content }
}

async function captureScreenFrame(stream: MediaStream) {
  const video = document.createElement('video')
  video.muted = true
  video.playsInline = true
  video.srcObject = stream
  try {
    await new Promise<void>((resolve, reject) => {
      video.onloadeddata = () => resolve()
      video.onerror = () => reject(new Error('Could not read the shared screen.'))
      if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) resolve()
      void video.play().catch(reject)
    })
    if (!video.videoWidth || !video.videoHeight) {
      throw new Error('The shared screen did not provide an image frame.')
    }
    const scale = Math.min(1, 1280 / video.videoWidth)
    let canvas = document.createElement('canvas')
    canvas.width = Math.round(video.videoWidth * scale)
    canvas.height = Math.round(video.videoHeight * scale)
    const context = canvas.getContext('2d')
    if (!context) throw new Error('Screen capture is unavailable in this browser.')
    context.drawImage(video, 0, 0, canvas.width, canvas.height)
    let imageData = canvas.toDataURL('image/jpeg', 0.68)
    while (imageData.length > 3_300_000 && canvas.width > 640) {
      const smaller = document.createElement('canvas')
      smaller.width = Math.round(canvas.width * 0.75)
      smaller.height = Math.round(canvas.height * 0.75)
      const smallerContext = smaller.getContext('2d')
      if (!smallerContext) throw new Error('Screen capture is unavailable in this browser.')
      smallerContext.drawImage(canvas, 0, 0, smaller.width, smaller.height)
      canvas = smaller
      imageData = canvas.toDataURL('image/jpeg', 0.55)
    }
    if (imageData.length > 3_300_000) {
      throw new Error('The screen snapshot is too large to send. Stop sharing or reduce the screen resolution.')
    }
    return imageData
  } finally {
    video.pause()
    video.srcObject = null
  }
}

function App() {
  const { session, signOut, isGuest } = useAuth()
  const sessionUserId = session?.user.id ?? (isGuest ? 'guest' : '')
  const [conversations, setConversations] = useState<Conversation[]>(() => {
    try {
      return readConversations(session?.user.id ?? (isGuest ? 'guest' : ''))
    } catch (error) {
      console.error('Could not restore saved AI ANS chats.', error)
      return []
    }
  })
  const [activeId, setActiveId] = useState<string | null>(null)
  const [models, setModels] = useState<Model[]>([
    { id: 'demo', name: 'Demo mode', provider: 'AI ANS', configured: true },
  ])
  const [providerPresets, setProviderPresets] = useState<ProviderPreset[]>([])
  const [providerCatalogError, setProviderCatalogError] = useState('')
  const [selectedModel, setSelectedModel] = useState(isGuest ? 'demo' : 'gemini')
  const [draft, setDraft] = useState('')
  const [search, setSearch] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [libraryOpen, setLibraryOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [providersOpen, setProvidersOpen] = useState(false)
  const [providerToConfigure, setProviderToConfigure] = useState('')
  const [incidentUiState, setIncidentUiState] = useState<IncidentUiState>({
    ownerId: sessionUserId,
    files: [],
    mode: false,
    open: false,
  })
  const [screenShareState, setScreenShareState] = useState<ScreenShareState | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [modelsLoaded, setModelsLoaded] = useState(false)
  const [isListening, setIsListening] = useState(false)
  const [speakingMessageId, setSpeakingMessageId] = useState('')
  const [messageFeedback, setMessageFeedback] = useState<Record<string, 'helpful' | 'not-helpful'>>({})
  const storageError = useSyncExternalStore(
    subscribeToStorageStatus,
    getStorageStatus,
    () => false,
  )
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const transcriptRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const speechRecognitionRef = useRef<BrowserSpeechRecognition | null>(null)
  const screenStreamRef = useRef<MediaStream | null>(null)
  const modelSelectionInitialized = useRef(false)
  const incidentUi = incidentUiState.ownerId === sessionUserId
    ? incidentUiState
    : { ownerId: sessionUserId, files: [], mode: false, open: false }
  const incidentFiles = incidentUi.files
  const incidentMode = incidentUi.mode
  const incidentsOpen = incidentUi.open
  const screenSharing = screenShareState?.ownerId === sessionUserId && screenShareState.stream.active
  const setIncidentsOpen = (open: boolean) => {
    setIncidentUiState((current) => ({
      ...(current.ownerId === sessionUserId
        ? current
        : { ownerId: sessionUserId, files: [], mode: false, open: false }),
      open,
    }))
  }
  const setIncidentMode = (value: boolean | ((current: boolean) => boolean)) => {
    setIncidentUiState((current) => {
      const state = current.ownerId === sessionUserId
        ? current
        : { ownerId: sessionUserId, files: [], mode: false, open: false }
      return { ...state, mode: typeof value === 'function' ? value(state.mode) : value }
    })
  }
  const handleIncidentFilesChanged = useCallback((files: IncidentFile[]) => {
    setIncidentUiState((current) => {
      const state = current.ownerId === sessionUserId
        ? current
        : { ownerId: sessionUserId, files: [], mode: false, open: false }
      return { ...state, files, mode: files.length === 0 ? false : state.mode }
    })
  }, [sessionUserId])

  const activeConversation = conversations.find((chat) => chat.id === activeId)
  const activeMessages = activeConversation?.messages ?? []
  const visibleConversations = conversations
    .filter((chat) => chat.title.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updatedAt - a.updatedAt)
  const selectedModelDetails = models.find((model) => model.id === selectedModel) ?? models[0]
  const isLive = selectedModelDetails?.id !== 'demo'

  useEffect(() => {
    if (!sessionUserId) return
    try {
      window.localStorage.setItem(
        `${STORAGE_KEY_PREFIX}${sessionUserId}`,
        JSON.stringify(conversations),
      )
      reportStorageStatus(false)
    } catch (error) {
      console.error('Could not save AI ANS chats in this browser.', error)
      reportStorageStatus(true)
    }
  }, [conversations, sessionUserId])

  useEffect(() => {
    screenStreamRef.current?.getTracks().forEach((track) => track.stop())
    screenStreamRef.current = null
  }, [sessionUserId])

  useEffect(() => {
    const loadModels = async () => {
      try {
        const response = await fetch('/api/v1/models', {
          headers: { Authorization: `Bearer ${session?.access_token ?? ''}` },
        })
        if (!response.ok) throw new Error(`Model list returned HTTP ${response.status}`)
        const payload = (await response.json()) as { models: Model[] }
        if (!payload.models?.length) throw new Error('The model list was empty.')
        setModels(payload.models)
        setSelectedModel((previous) => {
          if (modelSelectionInitialized.current && payload.models.some((model) => model.id === previous)) {
            return previous
          }
          modelSelectionInitialized.current = true
          return payload.models.find((model) => model.is_default)?.id
            ?? payload.models.find((model) => model.id === 'gemini')?.id
            ?? payload.models[0].id
        })
        setModelsLoaded(true)
        try {
          const catalogResponse = await fetch('/api/v1/providers/catalog', {
            headers: { Authorization: `****** ?? ''}` },
          })
          if (!catalogResponse.ok) {
            throw new Error(`Provider catalog returned HTTP ${catalogResponse.status}`)
          }
          const catalog = (await catalogResponse.json()) as { providers: ProviderPreset[] }
          setProviderPresets(catalog.providers)
          setProviderCatalogError('')
        } catch (catalogError) {
          console.error('Could not load supported providers into the model picker.', catalogError)
          setProviderCatalogError('Provider list is unavailable. Open AI Providers to configure models.')
        }
      } catch (error) {
        console.error('Could not load the AI ANS model list.', error)
        setErrorMessage('Could not connect to the AI ANS backend. Start the server and try again.')
        setModelsLoaded(true)
      }
    }
    void loadModels()
  }, [session?.access_token, isGuest])

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        const chat = createConversation(selectedModel)
        setConversations((current) => [chat, ...current])
        setActiveId(chat.id)
        setErrorMessage('')
        setDraft('')
        window.setTimeout(() => composerRef.current?.focus(), 40)
      }
    }
    window.addEventListener('keydown', handleShortcut)
    return () => window.removeEventListener('keydown', handleShortcut)
  }, [selectedModel])

  useEffect(() => {
    if (!notice) return
    const timeout = window.setTimeout(() => setNotice(''), 2000)
    return () => window.clearTimeout(timeout)
  }, [notice])

  const latestMessageContent = activeMessages.at(-1)?.content
  useEffect(() => {
    transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: 'smooth' })
  }, [activeId, activeMessages.length, latestMessageContent])

  const updateConversation = (id: string, update: (chat: Conversation) => Conversation) => {
    setConversations((current) => current.map((chat) => (chat.id === id ? update(chat) : chat)))
  }

  const startNewChat = () => {
    const chat = createConversation(selectedModel)
    setConversations((current) => [chat, ...current])
    setActiveId(chat.id)
    setSidebarOpen(false)
    setErrorMessage('')
    setDraft('')
    window.setTimeout(() => composerRef.current?.focus(), 40)
  }

  const applyScreenAction = (chatId: string, messageId: string, action: ScreenAction) => {
    if (action.type === 'fill_draft') {
      setDraft(action.text)
      composerRef.current?.focus()
      setNotice('Suggested text added to the draft. Review it before sending.')
    } else {
      startNewChat()
      setNotice('Started a new AI ANS chat.')
    }
    updateConversation(chatId, (chat) => ({
      ...chat,
      messages: chat.messages.map((message) =>
        message.id === messageId ? { ...message, screenActions: [] } : message,
      ),
    }))
  }

  const appendToMessage = (chatId: string, messageId: string, text: string) => {
    const updatedAt = currentTimestamp()
    updateConversation(chatId, (chat) => ({
      ...chat,
      updatedAt,
      messages: chat.messages.map((message) =>
        message.id === messageId ? { ...message, content: message.content + text } : message,
      ),
    }))
  }

  const sendMessage = async (value: string) => {
    const content = value.trim()
    if (!content || isSending || !modelsLoaded) return

    const chat = activeConversation ?? createConversation(selectedModel)
    const chatId = chat.id
    const assistantId = crypto.randomUUID()
    const messages = [
      ...chat.messages,
      { id: crypto.randomUUID(), role: 'user' as const, content },
      { id: assistantId, role: 'assistant' as const, content: '' },
    ]
    const updatedChat: Conversation = {
      ...chat,
      title: chat.messages.length ? chat.title : content.slice(0, 48),
      model: selectedModel,
      updatedAt: currentTimestamp(),
      messages,
    }

    setConversations((current) =>
      current.some((item) => item.id === chatId)
        ? current.map((item) => (item.id === chatId ? updatedChat : item))
        : [updatedChat, ...current],
    )
    setActiveId(chatId)
    setDraft('')
    setErrorMessage('')
    if (isGuest && selectedModel === 'demo') {
      const demoReply = 'This guest workspace saves your message only in this browser. No AI provider was called. Sign in and connect a provider to get an AI-generated answer.'
      setConversations((current) =>
        current.map((item) => item.id === chatId
          ? {
              ...item,
              messages: item.messages.map((message) =>
                message.id === assistantId ? { ...message, content: demoReply } : message,
              ),
            }
          : item),
      )
      return
    }
    setIsSending(true)
    const controller = new AbortController()
    abortRef.current = controller

    try {
      const imageData = screenStreamRef.current
        ? await captureScreenFrame(screenStreamRef.current)
        : undefined
      const response = await fetch('/api/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${session?.access_token ?? ''}`,
        },
        signal: controller.signal,
        body: JSON.stringify({
          model: selectedModel,
          provider_id: selectedModelDetails?.provider_id,
          incident_rag: incidentMode,
          ...(imageData ? { image_data: imageData } : {}),
          messages: messages
            .filter((message) => message.content)
            .slice(-20)
            .map(({ role, content: messageContent }) => ({ role, content: messageContent })),
        }),
      })
      if (!response.ok) {
        const responseText = await response.text()
        let detail = `The chat request failed (HTTP ${response.status}).`
        if (responseText) {
          try {
            const result = JSON.parse(responseText) as { detail?: string }
            detail = result.detail ?? detail
          } catch (error) {
            if (!(error instanceof SyntaxError)) throw error
          }
        }
        throw new Error(detail)
      }
      if (!response.body) throw new Error('The server did not return a response stream.')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let finished = false
      let assistantContent = ''

      while (!finished) {
        const { value: chunk, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(chunk, { stream: true })
        const events = buffer.split('\n\n')
        buffer = events.pop() ?? ''

        for (const rawEvent of events) {
          const data = rawEvent
            .split('\n')
            .find((line) => line.startsWith('data: '))
            ?.slice(6)
          if (!data) continue
          const event = JSON.parse(data) as StreamEvent
          if (event.type === 'token') {
            assistantContent += event.text
            appendToMessage(chatId, assistantId, event.text)
          }
          if (event.type === 'error') {
            setErrorMessage(event.text)
            finished = true
            await reader.cancel()
            break
          }
          if (event.type === 'done') {
            const result = imageData
              ? extractScreenAction(assistantContent)
              : { content: assistantContent }
            updateConversation(chatId, (current) => ({
              ...current,
              messages: current.messages.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      content: result.content,
                      screenActions: result.action ? [result.action] : [],
                    }
                  : message,
              ),
            }))
            finished = true
            await reader.cancel()
            break
          }
        }
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      console.error('AI ANS could not send this message.', error)
      setErrorMessage(
        error instanceof Error ? error.message : 'Something went wrong while sending your message.',
      )
    } finally {
      abortRef.current = null
      setIsSending(false)
    }
  }

  const stopGeneration = () => abortRef.current?.abort()

  const toggleScreenSharing = async () => {
    if (screenStreamRef.current) {
      screenStreamRef.current.getTracks().forEach((track) => track.stop())
      screenStreamRef.current = null
      setScreenShareState(null)
      setNotice('Screen sharing stopped.')
      return
    }
    if (!navigator.mediaDevices?.getDisplayMedia) {
      setErrorMessage('Screen sharing is not supported in this browser. Try a current version of Chrome or Edge.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false })
      const track = stream.getVideoTracks()[0]
      if (!track) {
        stream.getTracks().forEach((item) => item.stop())
        throw new Error('No screen was selected.')
      }
      screenStreamRef.current = stream
      setScreenShareState({ ownerId: sessionUserId, stream })
      track.addEventListener('ended', () => {
        if (screenStreamRef.current === stream) {
          screenStreamRef.current = null
          setScreenShareState(null)
        }
      }, { once: true })
      setErrorMessage('')
      setNotice('Screen shared. A snapshot is sent only with your next message.')
    } catch (error) {
      console.error('Could not start desktop sharing.', error)
      setErrorMessage(
        error instanceof DOMException && error.name === 'NotAllowedError'
          ? 'Screen sharing was cancelled or permission was denied.'
          : error instanceof Error ? error.message : 'Could not start screen sharing.',
      )
    }
  }

  const toggleVoiceInput = () => {
    if (speechRecognitionRef.current) {
      speechRecognitionRef.current.stop()
      return
    }
    const speechWindow = window as SpeechRecognitionWindow
    const SpeechRecognition = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition
    if (!SpeechRecognition) {
      setErrorMessage('Voice input is not supported in this browser. Try a current version of Chrome or Edge.')
      return
    }
    const recognition = new SpeechRecognition()
    recognition.lang = navigator.language || 'en-US'
    recognition.interimResults = false
    recognition.onresult = (event) => {
      const transcript = Array.from(event.results)
        .slice(event.resultIndex)
        .map((result) => result[0]?.transcript ?? '')
        .join('')
        .trim()
      if (transcript) {
        setDraft((current) => `${current.trimEnd()}${current.trim() ? ' ' : ''}${transcript}`)
      }
    }
    recognition.onerror = (event) => {
      if (event.error !== 'no-speech' && event.error !== 'aborted') {
        setErrorMessage(`Voice input failed: ${event.error}. Check your microphone permission and try again.`)
      }
    }
    recognition.onend = () => {
      speechRecognitionRef.current = null
      setIsListening(false)
    }
    speechRecognitionRef.current = recognition
    setErrorMessage('')
    setIsListening(true)
    try {
      recognition.start()
    } catch (error) {
      speechRecognitionRef.current = null
      setIsListening(false)
      console.error('Could not start browser voice input.', error)
      setErrorMessage('Could not start voice input. Check your microphone permission and try again.')
    }
  }

  const readMessageAloud = (messageId: string, content: string) => {
    if (!('speechSynthesis' in window)) {
      setErrorMessage('Speech playback is not supported in this browser.')
      return
    }
    if (speakingMessageId === messageId) {
      window.speechSynthesis.cancel()
      setSpeakingMessageId('')
      return
    }
    window.speechSynthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(content)
    utterance.onend = () => setSpeakingMessageId('')
    utterance.onerror = () => {
      setSpeakingMessageId('')
      setErrorMessage('Could not play this response aloud.')
    }
    setSpeakingMessageId(messageId)
    window.speechSynthesis.speak(utterance)
  }

  const attachTextFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return
    if (file.size > 1_000_000) {
      setErrorMessage('Text files must be smaller than 1 MB.')
      return
    }
    if (file.type && !file.type.startsWith('text/') && !/\.(txt|md|csv|json|py|js|ts|tsx|html|css|xml|yaml|yml|log)$/i.test(file.name)) {
      setErrorMessage('Only text, code, Markdown, CSV, and JSON files are supported.')
      return
    }
    try {
      const text = await file.text()
      const excerpt = text.slice(0, 3_000)
      const prompt = `Please analyze the attached file "${file.name}":\n\n${excerpt}`
      setDraft(prompt.slice(0, 4_000) + (text.length > excerpt.length ? '\n\n[File excerpt truncated.]' : ''))
      setErrorMessage('')
      setNotice(text.length > excerpt.length ? 'File excerpt added (truncated to fit the chat).' : 'File content added to your draft.')
      composerRef.current?.focus()
    } catch (error) {
      console.error('Could not read the selected text file.', error)
      setErrorMessage('Could not read this file. Try a plain-text or supported code file.')
    }
  }

  useEffect(() => () => {
    speechRecognitionRef.current?.stop()
    window.speechSynthesis?.cancel()
    screenStreamRef.current?.getTracks().forEach((track) => track.stop())
  }, [])

  const openProviderSettings = (providerId = '') => {
    setProviderToConfigure(providerId)
    setProvidersOpen(true)
    setModelMenuOpen(false)
  }

  const copyMessage = async (content: string) => {
    try {
      await navigator.clipboard.writeText(content)
      setNotice('Copied to clipboard')
      window.setTimeout(() => setNotice(''), 1800)
    } catch (error) {
      console.error('Could not copy the AI ANS response.', error)
      setErrorMessage('Clipboard access is unavailable in this browser.')
    }
  }

  const handleComposerKey = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void sendMessage(draft)
    }
  }

  return (
    <div className="app-shell">
      <AnimatePresence>
        {sidebarOpen && (
          <motion.button
            aria-label="Close navigation"
            className="mobile-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => setSidebarOpen(false)}
          />
        )}
      </AnimatePresence>

      <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
        <div className="sidebar-top">
          <a className="brand" href="/" aria-label="AI ANS home">
            <span className="brand-mark"><span /></span>
            <span className="brand-name">AI <b>ANS</b><small>PERSONAL INTELLIGENCE</small></span>
          </a>
          <button className="icon-button mobile-close" aria-label="Close navigation" onClick={() => setSidebarOpen(false)}>
            <X size={17} />
          </button>
        </div>

        <button className="new-chat-button" onClick={startNewChat}>
          <SquarePen size={17} />
          <span>New chat</span>
          <span className="shortcut">⌘ K</span>
        </button>

        <div className="sidebar-actions">
          <button className="side-action active"><MessageSquareText size={17} /><span>Chat</span></button>
          <button className="side-action" onClick={() => { setSearchOpen((open) => !open); setSearch('') }}>
            <Search size={17} /><span>Search chats</span>
          </button>
          <button className="side-action" onClick={() => setLibraryOpen(true)}><Library size={17} /><span>My library</span></button>
          <button className="side-action" onClick={() => setIncidentsOpen(true)}><Database size={17} /><span>Incident RAG</span></button>
          <button
            className="side-action"
            title="Open a web search for your latest question"
            onClick={() => {
              const query = draft.trim() || activeMessages.filter((message) => message.role === 'user').at(-1)?.content
              if (!query) {
                setNotice('Enter a question first, then choose Research.')
                composerRef.current?.focus()
                return
              }
              if (window.confirm('This opens Google Search in a new tab and sends your search text to Google. Continue?')) {
                window.open(`https://www.google.com/search?q=${encodeURIComponent(query)}`, '_blank', 'noopener,noreferrer')
              }
            }}
          ><Globe2 size={17} /><span>Research</span></button>
          <button className="side-action" onClick={() => openProviderSettings()}><Cog size={17} /><span>AI Providers</span></button>
        </div>

        <div className="history-heading">
          <span>YOUR CHATS</span>
          <button className="icon-button compact" aria-label="Start a new chat" onClick={startNewChat}><Plus size={15} /></button>
        </div>
        {searchOpen && (
          <label className="search-field">
            <Search size={14} />
            <input autoFocus value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search conversations" />
          </label>
        )}
        <div className="history-list">
          {visibleConversations.length === 0 ? (
            <p className="empty-history">{search ? 'No matching chats' : 'Your conversations will appear here.'}</p>
          ) : (
            visibleConversations.map((chat) => (
              <div className={`history-item ${chat.id === activeId ? 'selected' : ''}`} key={chat.id}>
                <button className="history-select" onClick={() => { setActiveId(chat.id); setSidebarOpen(false); setErrorMessage('') }}>
                  {chat.pinned && <Pin size={12} className="pin-icon" />}
                  <span>{chat.title}</span>
                </button>
                <button
                  className="icon-button history-more"
                  title={chat.pinned ? 'Unpin chat' : 'Pin chat'}
                  aria-label={chat.pinned ? 'Unpin chat' : 'Pin chat'}
                  onClick={() => updateConversation(chat.id, (current) => ({ ...current, pinned: !current.pinned }))}
                >
                  <MoreHorizontal size={15} />
                </button>
              </div>
            ))
          )}
        </div>

        <div className="sidebar-bottom">
          <div className="privacy-card">
            <span className="privacy-icon"><Sparkles size={16} /></span>
            <div><strong>Your space, your rules.</strong><p>Chats stay in this browser.</p></div>
          </div>
          <div className="profile-row">
            <span className="avatar">A</span>
            <span className="profile-name">{isGuest ? 'Local workspace' : session?.user.email ?? 'Google account'}<small>{isGuest ? 'This device only' : 'Signed in'}</small></span>
              <button
                className="icon-button profile-menu"
                aria-label="Sign out"
                title={isGuest ? 'Exit guest mode' : 'Sign out'}
                onClick={() => void signOut().catch((error: unknown) => {
                  console.error('Could not sign out of AI ANS.', error)
                  setErrorMessage('Could not sign out. Please try again.')
                })}
              >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div className="topbar-left">
            <button className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setSidebarOpen(true)}><Menu size={19} /></button>
            <div className="breadcrumb"><span>Workspace</span><span className="crumb-slash">/</span><strong>{activeConversation?.title ?? 'New chat'}</strong></div>
          </div>
          <div className="topbar-right">
            <div className={`connection-pill ${selectedModelDetails?.status === 'connected' ? 'connected' : ''}`}><span />{selectedModelDetails?.provider ?? 'Demo mode'}{isLive && selectedModelDetails?.status !== 'connected' ? ' · not tested' : ''}</div>
            <button className="upgrade-button" onClick={() => openProviderSettings()}>AI Providers <span>↗</span></button>
            <button className="icon-button help-button" aria-label="About AI ANS" title="About and help" onClick={() => setHelpOpen(true)}><span>?</span></button>
          </div>
        </header>

        <section className={`chat-area ${activeMessages.length ? 'has-messages' : ''}`}>
          {activeMessages.length === 0 ? (
            <motion.div
              className="welcome"
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.45 }}
            >
              <div className="welcome-orb"><div className="orb-core" /><span className="orb-ring ring-one" /><span className="orb-ring ring-two" /></div>
              <div className="eyebrow"><span className="eyebrow-dot" /> YOUR INTELLIGENCE, UNIFIED</div>
              <h1>One question.<br /><span>Unlimited intelligence.</span></h1>
              <p className="welcome-copy">A clearer way to think, create, and find your next great idea.</p>
              <div className="suggestions">
                {SUGGESTIONS.map(({ icon: Icon, label, prompt }) => (
                  <button className="suggestion-card" key={label} onClick={() => { setDraft(prompt); composerRef.current?.focus() }}>
                    <span className="suggestion-icon"><Icon size={16} /></span>
                    <span>{label}</span>
                    <span className="suggestion-arrow">↗</span>
                  </button>
                ))}
              </div>
              {!isLive && (
                <button className="setup-hint" onClick={() => openProviderSettings()}>
                  <span><Sparkles size={14} /> Want genuine AI answers?</span>
                  <strong>Connect an AI provider <span aria-hidden="true">→</span></strong>
                </button>
              )}
            </motion.div>
          ) : (
            <div className="transcript" ref={transcriptRef} aria-live="polite">
              {activeMessages.map((message) => (
                <motion.article
                  className={`message message-${message.role}`}
                  key={message.id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  {message.role === 'assistant' ? (
                    <div className="assistant-avatar"><span className="brand-mark small"><span /></span></div>
                  ) : <div className="user-avatar">A</div>}
                  <div className="message-main">
                    <div className="message-heading">{message.role === 'assistant' ? 'AI ANS' : 'You'}{message.role === 'assistant' && <span className="model-badge">{selectedModelDetails?.name ?? 'AI ANS'}</span>}</div>
                    <div className="message-content">
                      {message.content ? (
                        <Suspense fallback={<p>Loading response…</p>}>
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                        </Suspense>
                      ) : isSending ? <span className="typing-indicator"><i /><i /><i /></span> : null}
                    </div>
                    {message.role === 'assistant' && message.content && (
                      <div className="message-tools">
                        <button className="icon-button" title="Copy response" aria-label="Copy response" onClick={() => void copyMessage(message.content)}><Copy size={14} /></button>
                        <button
                          className={`icon-button ${speakingMessageId === message.id ? 'active' : ''}`}
                          title={speakingMessageId === message.id ? 'Stop speaking' : 'Read response aloud'}
                          aria-label={speakingMessageId === message.id ? 'Stop speaking' : 'Read response aloud'}
                          onClick={() => readMessageAloud(message.id, message.content)}
                        ><Volume2 size={14} /></button>
                        <button
                          className={`icon-button ${messageFeedback[message.id] === 'helpful' ? 'active' : ''}`}
                          title="Helpful"
                          aria-label="Mark response helpful"
                          aria-pressed={messageFeedback[message.id] === 'helpful'}
                          onClick={() => {
                            setMessageFeedback((current) => ({ ...current, [message.id]: 'helpful' }))
                            setNotice('Feedback noted for this session.')
                          }}
                        ><ThumbsUp size={14} /></button>
                        <button
                          className={`icon-button ${messageFeedback[message.id] === 'not-helpful' ? 'active' : ''}`}
                          title="Not helpful"
                          aria-label="Mark response not helpful"
                          aria-pressed={messageFeedback[message.id] === 'not-helpful'}
                          onClick={() => {
                            setMessageFeedback((current) => ({ ...current, [message.id]: 'not-helpful' }))
                            setNotice('Feedback noted for this session.')
                          }}
                        ><ThumbsDown size={14} /></button>
                      </div>
                    )}
                    {message.role === 'assistant' && message.screenActions?.map((action, index) => (
                      <div className="screen-action-card" key={`${message.id}-${index}`}>
                        <span>
                          {action.type === 'fill_draft'
                            ? <>Fill the AI ANS draft with: <code>{action.text}</code></>
                            : 'Start a new AI ANS chat'}
                        </span>
                        <button
                          className="settings-small-button"
                          onClick={() => applyScreenAction(activeConversation?.id ?? '', message.id, action)}
                        >Confirm & apply</button>
                      </div>
                    ))}
                  </div>
                </motion.article>
              ))}
              {errorMessage && (
                <div className="error-banner" role="alert">
                  <span>{errorMessage}</span>
                  {errorMessage.includes('API key') && (
                    <button className="error-action" onClick={() => openProviderSettings()}>Check provider settings</button>
                  )}
                  {errorMessage.includes('aistudio.google.com/billing') && (
                    <a className="error-action" href="https://aistudio.google.com/billing" target="_blank" rel="noreferrer">Check Google billing</a>
                  )}
                </div>
              )}
              {storageError && <div className="error-banner" role="status">Browser storage is unavailable. This chat may not be saved.</div>}
            </div>
          )}

          <div className="composer-wrap">
            {activeMessages.length === 0 && storageError && <div className="error-banner storage-banner" role="status">Browser storage is unavailable. Chats may not be saved.</div>}
            {errorMessage && activeMessages.length === 0 && <div className="error-banner storage-banner" role="alert">{errorMessage}</div>}
            <div className="composer">
              <textarea
                ref={composerRef}
                value={draft}
                maxLength={4000}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={handleComposerKey}
                placeholder="Ask anything. Start anywhere."
                aria-label="Message AI ANS"
                rows={1}
              />
              <div className="composer-toolbar">
                <div className="composer-options">
                  <div className="model-picker-wrap">
                    <button className="model-picker" onClick={() => setModelMenuOpen((open) => !open)} aria-expanded={modelMenuOpen}>
                      <span className="model-icon"><Sparkles size={13} /></span>
                      <span>{selectedModelDetails?.name ?? 'Demo mode'}</span>
                      <ChevronDown size={14} />
                    </button>
                    <AnimatePresence>
                      {modelMenuOpen && (
                        <motion.div className="model-menu" initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 5 }}>
                          <span className="menu-label">YOUR MODELS</span>
                          {models.map((model) => (
                            <button key={model.id} className="model-choice" onClick={() => { setSelectedModel(model.id); setModelMenuOpen(false) }}>
                              <span className="model-icon"><Sparkles size={13} /></span>
                              <span>{model.name}<small>{model.provider}</small></span>
                              {selectedModel === model.id && <Check size={15} className="model-check" />}
                            </button>
                          ))}
                          {providerPresets.length > 0 && (
                            <>
                              <span className="menu-label">ALL SUPPORTED PROVIDERS</span>
                              {providerPresets.map((provider) => {
                                const configured = models.some((model) => model.provider_type === provider.id)
                                return (
                                  <button
                                    key={provider.id}
                                    className="model-choice provider-choice"
                                    onClick={() => openProviderSettings(provider.id)}
                                  >
                                    <span className="model-icon"><Sparkles size={13} /></span>
                                    <span>{provider.name}<small>{configured ? 'Add another model or key' : 'Set up provider to use its models'}</small></span>
                                    <Plus size={14} className="provider-add-icon" />
                                  </button>
                                )
                              })}
                            </>
                          )}
                          {providerCatalogError && <p className="model-note">{providerCatalogError}</p>}
                          <p className="model-note">Only connected models can answer chats. Provider keys are configured in AI Providers.</p>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                  <span className="composer-divider" />
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".txt,.md,.csv,.json,.py,.js,.ts,.tsx,.html,.css,.xml,.yaml,.yml,.log,text/*"
                    onChange={(event) => void attachTextFile(event)}
                    tabIndex={-1}
                    aria-hidden="true"
                    hidden
                  />
                  <button className="composer-tool" title="Attach a text or code file" aria-label="Attach a text or code file" onClick={() => fileInputRef.current?.click()}><Plus size={17} /></button>
                  <button
                    className={`composer-tool ${incidentMode ? 'active' : ''}`}
                    title={incidentMode ? 'Turn off incident search' : 'Use incident search'}
                    aria-label={incidentMode ? 'Turn off incident search' : 'Use incident search'}
                    aria-pressed={incidentMode}
                    onClick={() => {
                      if (incidentFiles.length === 0) {
                        setIncidentsOpen(true)
                        setNotice('Upload incident files to enable incident search.')
                      } else {
                        setIncidentMode((enabled) => !enabled)
                      }
                    }}
                  ><Database size={16} /></button>
                  <button
                    className={`composer-tool ${screenSharing ? 'active' : ''}`}
                    title={screenSharing ? 'Stop sharing screen' : 'Share desktop for screen suggestions'}
                    aria-label={screenSharing ? 'Stop sharing screen' : 'Share desktop for screen suggestions'}
                    aria-pressed={screenSharing}
                    onClick={() => void toggleScreenSharing()}
                  >{screenSharing ? <EyeOff size={16} /> : <Eye size={16} />}</button>
                </div>
                <div className="send-actions">
                  <button
                    className={`voice-button ${isListening ? 'voice-listening' : ''}`}
                    aria-label={isListening ? 'Stop voice input' : 'Start voice input'}
                    title={isListening ? 'Stop voice input' : 'Dictate a message'}
                    onClick={toggleVoiceInput}
                  ><AudioLines size={17} /></button>
                  {isSending ? (
                    <button className="send-button stop-button" onClick={stopGeneration} aria-label="Stop generating"><Square size={13} fill="currentColor" /></button>
                  ) : (
                    <button className="send-button" onClick={() => void sendMessage(draft)} disabled={!modelsLoaded || !draft.trim()} aria-label="Send message"><ArrowUp size={18} /></button>
                  )}
                </div>
              </div>
            </div>
            {(incidentMode || screenSharing) && (
              <div className="composer-assist-status" role="status">
                {incidentMode && <span>Incident search on · {incidentFiles.length} file{incidentFiles.length === 1 ? '' : 's'}</span>}
                {screenSharing && <span>Screen shared · snapshots only sent with your messages; AI suggests actions but does not control your screen.</span>}
              </div>
            )}
            <div className="composer-disclaimer">
              <span><Sparkles size={12} /> AI can make mistakes. Check important information.</span>
              <span>Press <kbd>Enter</kbd> to send <i>·</i> <kbd>Shift + Enter</kbd> for a new line</span>
            </div>
          </div>
        </section>

        <footer className="main-footer">
          <span>© 2026 AI ANS <i>·</i> Built for the curious.</span>
          <span>Thoughtful AI, with you in control <Sparkles size={12} /></span>
        </footer>
      </main>

      <AnimatePresence>
        {notice && <motion.div className="toast" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>{notice}</motion.div>}
      </AnimatePresence>

      {libraryOpen && (
        <div className="utility-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setLibraryOpen(false)
        }}>
          <section className="utility-dialog" role="dialog" aria-modal="true" aria-labelledby="library-title">
            <button className="icon-button utility-close" aria-label="Close library" onClick={() => setLibraryOpen(false)}><X size={17} /></button>
            <span className="settings-eyebrow">YOUR SAVED CONVERSATIONS</span>
            <h2 id="library-title">My library</h2>
            <p>Pin a conversation from your chat list to save it here.</p>
            {conversations.some((chat) => chat.pinned) ? (
              <div className="library-list">
                {conversations.filter((chat) => chat.pinned).map((chat) => (
                  <button
                    className="library-item"
                    key={chat.id}
                    onClick={() => {
                      setActiveId(chat.id)
                      setLibraryOpen(false)
                    }}
                  >
                    <Pin size={14} />
                    <span>{chat.title}<small>{chat.messages.length} messages</small></span>
                  </button>
                ))}
              </div>
            ) : <p className="library-empty">No saved chats yet. Use the pin button next to a conversation to add it.</p>}
          </section>
        </div>
      )}

      {helpOpen && (
        <div className="utility-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setHelpOpen(false)
        }}>
          <section className="utility-dialog" role="dialog" aria-modal="true" aria-labelledby="help-title">
            <button className="icon-button utility-close" aria-label="Close help" onClick={() => setHelpOpen(false)}><X size={17} /></button>
            <span className="settings-eyebrow">HELP & SHORTCUTS</span>
            <h2 id="help-title">About AI ANS</h2>
            <p>Choose a connected AI model, search uploaded incident reports, or share your desktop for read-only screen suggestions. Screenshots are sent only with a message, and the assistant does not control your screen.</p>
            <div className="help-shortcuts"><kbd>Enter</kbd><span>Send message</span><kbd>Shift + Enter</kbd><span>New line</span><kbd>Ctrl + K</kbd><span>Start a new chat</span></div>
            {isGuest && <p className="library-empty">Guest mode works only from this computer. The local Gemini key, if configured, stays on the backend; public deployments require Google sign-in.</p>}
          </section>
        </div>
      )}

      <ProvidersSettings
        open={providersOpen}
        accessToken={session?.access_token ?? ''}
        initialProviderId={providerToConfigure}
        onClose={() => {
          setProvidersOpen(false)
          setProviderToConfigure('')
        }}
        onModelsChanged={() => {
          void fetch('/api/v1/models', {
            headers: { Authorization: `Bearer ${session?.access_token ?? ''}` },
          })
            .then((response) => {
              if (!response.ok) throw new Error(`Model list returned HTTP ${response.status}`)
              return response.json() as Promise<{ models: Model[] }>
            })
            .then((payload) => {
              setModels(payload.models)
              setSelectedModel((current) =>
                current !== 'demo' && payload.models.some((model) => model.id === current)
                  ? current
                  : payload.models.find((model) => model.is_default)?.id ?? payload.models[0]?.id ?? 'demo',
              )
            })
            .catch((error: unknown) => {
              console.error('Could not refresh the AI ANS model list.', error)
            })
        }}
      />
      <IncidentsSettings
        open={incidentsOpen}
        accessToken={session?.access_token ?? ''}
        onClose={() => setIncidentsOpen(false)}
        onFilesChanged={handleIncidentFilesChanged}
      />
    </div>
  )
}

export default App
