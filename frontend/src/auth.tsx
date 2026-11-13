import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ArrowRight, LoaderCircle, ShieldCheck } from 'lucide-react'
import type { Session } from '@supabase/supabase-js'
import { AuthContext, type AuthContextValue } from './auth-context'
import { supabase } from './supabase'

type Props = { children: ReactNode }

export function AuthProvider({ children }: Props) {
  const [session, setSession] = useState<Session | null>(null)
  const [isGuest, setIsGuest] = useState(false)
  const [loading, setLoading] = useState(Boolean(supabase))
  const [error, setError] = useState('')
  const [signingIn, setSigningIn] = useState(false)

  useEffect(() => {
    if (!supabase) return
    let active = true
    void supabase.auth.getSession()
      .then(({ data, error: sessionError }) => {
        if (!active) return
        if (sessionError) setError('Could not restore your sign-in. Please try again.')
        setSession(data.session)
        setLoading(false)
      })
      .catch(() => {
        if (!active) return
        setError('Could not restore your sign-in. Please try again.')
        setLoading(false)
      })
    const { data: listener } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession)
      setIsGuest(false)
      setLoading(false)
      setError('')
    })
    return () => {
      active = false
      listener.subscription.unsubscribe()
    }
  }, [])

  const value = useMemo<AuthContextValue>(() => ({
    session,
    isGuest,
    loading,
    configured: Boolean(supabase),
    signOut: async () => {
      setIsGuest(false)
      if (!supabase) return
      const { error: signOutError } = await supabase.auth.signOut()
      if (signOutError) throw signOutError
    },
  }), [session, isGuest, loading])

  const signInWithGoogle = async () => {
    if (!supabase) return
    setSigningIn(true)
    setError('')
    try {
      const { error: authError } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: {
          redirectTo: window.location.origin,
          scopes: 'openid email profile',
        },
      })
      if (authError) throw authError
    } catch (authError) {
      console.error('Google sign-in could not start.', authError)
      setError('Google sign-in could not start. Check that Google is enabled in Supabase Auth.')
      setSigningIn(false)
    }
  }

  if (loading) {
    return (
      <div className="auth-page">
        <div className="auth-card auth-loading">
          <LoaderCircle size={23} className="spin" />
          <p>Checking your secure sign-in…</p>
        </div>
      </div>
    )
  }

  if (!session) {
    if (isGuest) {
      return (
        <AuthContext.Provider value={value}>
          <Fragment key="guest">{children}</Fragment>
        </AuthContext.Provider>
      )
    }

    return (
      <div className="auth-page">
        <main className="auth-card">
          <span className="brand-mark auth-brand"><span /></span>
          <span className="settings-eyebrow">YOUR INTELLIGENCE, UNIFIED</span>
          <h1>Welcome to AI ANS</h1>
          <p>Sign in securely with your Google account to continue to your private AI workspace.</p>
          {!supabase && (
            <div className="auth-setup" role="status">
              <strong>Google sign-in needs setup</strong>
              <span>Set <code>VITE_SUPABASE_URL</code> and <code>VITE_SUPABASE_ANON_KEY</code> in <code>frontend\.env.local</code>, then enable Google in Supabase Auth.</span>
            </div>
          )}
          {error && <div className="settings-message settings-error" role="alert">{error}</div>}
          <button className="google-signin" onClick={() => void signInWithGoogle()} disabled={!supabase || signingIn}>
            {signingIn ? <LoaderCircle size={17} className="spin" /> : <GoogleMark />}
            Continue with Google
            <ArrowRight size={16} />
          </button>
          <button className="guest-signin" onClick={() => setIsGuest(true)}>
            Skip for now — continue with demo
          </button>
          <p className="guest-disclaimer">Local guest access is limited to this computer. Use Google sign-in for public or deployed workspaces.</p>
          <div className="auth-privacy"><ShieldCheck size={14} /> AI ANS never asks for or stores your Google password.</div>
        </main>
      </div>
    )
  }

  return (
    <AuthContext.Provider value={value}>
      <Fragment key={session.user.id}>{children}</Fragment>
    </AuthContext.Provider>
  )
}

function GoogleMark() {
  return <span aria-hidden="true" className="google-mark">G</span>
}
