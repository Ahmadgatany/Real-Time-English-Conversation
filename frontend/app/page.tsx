"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { PcmStreamPlayer } from "./pcm-stream-player";

type CapturedLatencyTurn = {
  end_of_speech_to_first_text_ms: number | null;
  backend_end_of_speech_to_first_gemini_audio_ms: number | null;
  backend_end_of_speech_to_first_audio_sent_ms: number | null;
  frontend_end_of_speech_to_first_audio_ms: number | null;
  frontend_end_of_speech_to_playback_start_ms: number | null;
  backend_audio_generation_complete_ms: number | null;
  backend_first_gemini_audio_to_generation_complete_ms: number | null;
  backend_gemini_first_chunk_to_first_send_ms: number | null;
  backend_last_audio_send_to_generation_complete_ms: number | null;
  frontend_audio_chunk_intervals_ms: number[];
  backend_gemini_audio_chunk_intervals_ms: number[];
  frontend_playback_complete_ms: number | null;
  backend_audio_chunk_intervals_ms: number[];
};

type User = { id: string; email: string; display_name: string; preferences: Preferences };
type Preferences = { voice: string; speaking_style: string; auto_start: boolean };
type Topic = { id: string; title: string; description: string; prompt: string };
type Conversation = { id: string; topic: string; mode: "general" | "ielts" | "practice"; started_at: string; ended_at: string | null; duration_seconds: number; summary: string | null; feedback: Record<string, unknown> };
type Evaluation = { id: string; conversation_id: string; scores: Record<string, number>; metrics: Record<string, unknown>; feedback: { strengths: string[]; weaknesses: string[]; examples: string[]; estimate_notice: string }; recommendations: string[]; coach_exercises: Exercise[]; is_ai_estimate: boolean; created_at: string };
type Exercise = { skill: string; prompt: string; target_duration_seconds: number };
type PracticeAttempt = { id: string; conversation_id: string; evaluation_id: string; skill: string; prompt: string; target_duration_seconds: number; transcript: string; metrics: Record<string, number>; comparison: { improvements?: string[]; remaining_weaknesses?: string[] } };
type Progress = { score_history: Array<{ created_at: string; scores: Record<string, number>; kind: string }>; trends: Record<string, { latest: number | null; change: number }>; recurring_weaknesses: string[]; recommended_next: Exercise; cost: { total_usd: number; records: number } };
type Line = { role: "user" | "assistant"; text: string };
type View = "home" | "conversation" | "evaluation" | "progress" | "history" | "profile" | "settings";

const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const websocketBase = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/conversation";

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, { ...options, credentials: "include", headers: { "Content-Type": "application/json", ...options.headers } });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail ?? "Something went wrong");
  return response.status === 204 ? (undefined as T) : response.json();
}

export default function HomePage() {
  const [user, setUser] = useState<User | null>(null);
  const [view, setView] = useState<View>("home");
  const [topics, setTopics] = useState<Topic[]>([]);
  const [history, setHistory] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [evaluationId, setEvaluationId] = useState<string | null>(null);
  const [practiceAttemptId, setPracticeAttemptId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([api<User>("/api/auth/me"), api<Topic[]>("/api/topics")])
      .then(([currentUser, availableTopics]) => { setUser(currentUser); setTopics(availableTopics); })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  async function signOut() {
    await api<void>("/api/auth/logout", { method: "POST" });
    setUser(null);
    setView("home");
  }

  async function startConversation(topic: string, mode: "general" | "ielts" = "general") {
    try {
      const conversation = await api<Conversation>("/api/conversations", { method: "POST", body: JSON.stringify({ topic, mode }) });
      setActiveConversation(conversation);
      setView("conversation");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not start conversation"); }
  }

  if (loading) return <main className="app-loading">Loading your space...</main>;
  if (!user) return <AuthScreen onAuthenticated={setUser} />;

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">say<span>.</span></div>
        <nav aria-label="Main navigation">
          {(["home", "conversation", "evaluation", "progress", "history", "profile", "settings"] as View[]).map((item) => (
            <button key={item} className={view === item ? "nav-item active" : "nav-item"} onClick={() => setView(item)}>
              <span className="nav-mark">{item === "home" ? "⌂" : item === "conversation" ? "◌" : item === "evaluation" ? "✦" : item === "progress" ? "↗" : item === "history" ? "◷" : item === "profile" ? "○" : "⋮"}</span>
              {item[0].toUpperCase() + item.slice(1)}
            </button>
          ))}
        </nav>
        <button className="sign-out" onClick={signOut}>Sign out</button>
      </aside>
      <section className="app-content">
        <header className="topbar"><span>{view === "evaluation" ? "IELTS evaluation" : view === "progress" ? "Progress" : view === "conversation" && activeConversation?.mode === "ielts" ? "IELTS Speaking" : "General conversation"}</span><span className="user-chip">{user.display_name.slice(0, 1).toUpperCase()}</span></header>
        {error && <div className="notice error">{error}<button onClick={() => setError("")}>Dismiss</button></div>}
        {view === "home" && <HomeView user={user} topics={topics} onStart={startConversation} onHistory={() => setView("history")} />}
        {view === "conversation" && activeConversation && <ConversationView conversation={activeConversation} onDone={(finished) => { if (finished.mode === "ielts") { setEvaluationId(finished.id); setView("evaluation"); } else if (finished.mode === "practice") { setView("evaluation"); } else { setView("history"); } setActiveConversation(null); }} />}
        {view === "evaluation" && evaluationId && <EvaluationView evaluationId={evaluationId} practiceAttemptId={practiceAttemptId} onStartPractice={(attempt) => { setPracticeAttemptId(attempt.id); setActiveConversation({ id: attempt.conversation_id, topic: attempt.prompt, mode: "practice", started_at: new Date().toISOString(), ended_at: null, duration_seconds: 0, summary: null, feedback: {} }); setView("conversation"); }} />}
        {view === "progress" && <ProgressView />}
        {view === "history" && <HistoryView onSelect={(conversation) => { if (conversation.mode === "ielts" && conversation.ended_at) { setEvaluationId(conversation.id); setView("evaluation"); } else { setActiveConversation(conversation); setView("conversation"); } }} />}
        {view === "profile" && <ProfileView user={user} onSaved={setUser} />}
        {view === "settings" && <SettingsView user={user} onSaved={setUser} />}
      </section>
    </main>
  );
}

function AuthScreen({ onAuthenticated }: { onAuthenticated: (user: User) => void }) {
  const [mode, setMode] = useState<"login" | "signup">("signup");
  const [email, setEmail] = useState(""); const [password, setPassword] = useState(""); const [name, setName] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { onAuthenticated(await api<User>(`/api/auth/${mode}`, { method: "POST", body: JSON.stringify({ email, password, display_name: name }) })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not authenticate"); } finally { setBusy(false); }
  }
  return <main className="auth-shell"><div className="auth-card"><div className="brand">say<span>.</span></div><p className="eyebrow">A better way to speak</p><h1>Find your voice<br /><i>in conversation.</i></h1><p className="auth-copy">Practice English out loud with a patient, natural conversation partner.</p><form onSubmit={submit}>{mode === "signup" && <input required placeholder="Your name" value={name} onChange={(event) => setName(event.target.value)} />}<input required type="email" placeholder="Email address" value={email} onChange={(event) => setEmail(event.target.value)} /><input required minLength={8} type="password" placeholder="Password (8+ characters)" value={password} onChange={(event) => setPassword(event.target.value)} />{error && <p className="form-error">{error}</p>}<button className="primary-button" disabled={busy}>{busy ? "Opening..." : mode === "signup" ? "Create account" : "Log in"}</button></form><button className="switch-auth" onClick={() => setMode(mode === "signup" ? "login" : "signup")}>{mode === "signup" ? "Already have an account? Log in" : "New here? Create an account"}</button></div></main>;
}

function HomeView({ user, topics, onStart, onHistory }: { user: User; topics: Topic[]; onStart: (topic: string, mode?: "general" | "ielts") => void; onHistory: () => void }) {
  return <div className="page"><div className="hero-row"><div><p className="eyebrow">Good to see you, {user.display_name.split(" ")[0]}</p><h1>What would you<br /><i>like to talk about?</i></h1></div><div className="hero-note">Your English gets better<br />one real conversation at a time.</div></div><section className="start-panel"><div><span className="panel-kicker">START A SESSION</span><h2>Choose your mode.</h2><p>Practice freely or take a timed IELTS speaking exam.</p></div><div><button className="free-button" onClick={() => onStart("Free Conversation", "general")}>General conversation <span>↗</span></button><button className="free-button" onClick={() => onStart("IELTS Speaking", "ielts")}>IELTS Speaking exam <span>↗</span></button></div></section><div className="section-heading"><h2>Suggested topics</h2><button onClick={onHistory}>View history →</button></div><div className="topic-grid">{topics.map((topic) => <button className="topic-card" key={topic.id} onClick={() => onStart(topic.title, "general")}><span className="topic-number">0{topics.indexOf(topic) + 1}</span><strong>{topic.title}</strong><p>{topic.description}</p><span className="topic-arrow">↗</span></button>)}</div><div className="future-strip"><span>AVAILABLE NOW</span><strong>IELTS Speaking exam</strong><strong>General conversation</strong><em>Choose a mode above to begin.</em></div></div>;
}

function ConversationView({ conversation, onDone }: { conversation: Conversation; onDone: (conversation: Conversation) => void }) {
  const [connected, setConnected] = useState(false); const [recording, setRecording] = useState(false); const [status, setStatus] = useState("Preparing your conversation"); const [lines, setLines] = useState<Line[]>([]); const [seconds, setSeconds] = useState(0); const [examState, setExamState] = useState("START"); const [examDeadline, setExamDeadline] = useState<number | null>(null); const [examRemaining, setExamRemaining] = useState<number | null>(null);
  const [latencies, setLatencies] = useState<number[]>([]); const [latestLatency, setLatestLatency] = useState<{ ttft?: number; firstAudio?: number; playbackStart?: number; playbackComplete?: number } | null>(null);
  const [latestBackendLatency, setLatestBackendLatency] = useState<Record<string, unknown> | null>(null);
  const [benchmarkTurns, setBenchmarkTurns] = useState<CapturedLatencyTurn[]>([]);
  const socketRef = useRef<WebSocket | null>(null); const streamRef = useRef<MediaStream | null>(null); const recorderRef = useRef<MediaRecorder | null>(null); const audioContextRef = useRef<AudioContext | null>(null); const audioSourceRef = useRef<AudioBufferSourceNode | null>(null); const audioQueueRef = useRef<Blob[]>([]); const audioPlayingRef = useRef(false); const audioGenerationRef = useRef(0); const browserSpeechRef = useRef(false); const chunksRef = useRef<Blob[]>([]); const responseChunksRef = useRef<Blob[]>([]); const lastAssistantTextRef = useRef(""); const responseMimeRef = useRef("audio/wav"); const startedAtRef = useRef(Date.now()); const connectionAttemptRef = useRef(0); const liveTransportRef = useRef(false); const recordingRef = useRef(false); const liveActivityRef = useRef(false); const pcmSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set()); const nextPcmStartRef = useRef(0); const liveAudioEndedRef = useRef(false); const captureSourceRef = useRef<MediaStreamAudioSourceNode | null>(null); const captureNodeRef = useRef<ScriptProcessorNode | null>(null); const clientTurnStartRef = useRef<number | null>(null); const firstAudioReceivedRef = useRef<number | null>(null); const turnTextAtRef = useRef<number | null>(null); const playbackStartedRef = useRef(false); const pcmStreamPlayerRef = useRef<PcmStreamPlayer | null>(null); const streamingPcmRef = useRef(false); const pendingPcmSequenceRef = useRef<number | null>(null); const activePcmStreamIdRef = useRef<number | null>(null); const nextClientPcmSequenceRef = useRef(0); const previousClientAudioAtRef = useRef<number | null>(null); const currentTurnRef = useRef<{ start: number; firstText?: number; firstAudio?: number; playbackStart?: number; playbackComplete?: number; chunkIntervals: number[]; backend?: Record<string, unknown> } | null>(null); const benchmarkMetadataRef = useRef<Record<string, unknown>>({});
  useEffect(() => { const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - startedAtRef.current) / 1000)), 1000); const attempt = ++connectionAttemptRef.current; start(attempt); return () => { window.clearInterval(timer); connectionAttemptRef.current += 1; cleanup(); }; }, []);
  useEffect(() => { if (!examDeadline) { setExamRemaining(null); return; } const update = () => setExamRemaining(Math.max(0, Math.ceil((examDeadline - Date.now()) / 1000))); update(); const timer = window.setInterval(update, 250); return () => window.clearInterval(timer); }, [examDeadline]);
  async function start(attempt: number) { try { const stream = await navigator.mediaDevices.getUserMedia({ audio: true }); if (attempt !== connectionAttemptRef.current) { stream.getTracks().forEach((track) => track.stop()); return; } streamRef.current = stream; await ensureAudioContext().catch((error) => console.error("[conversation] Initial AudioContext setup failed", error)); const socket = new WebSocket(`${websocketBase}?conversation_id=${conversation.id}&mode=${conversation.mode}&voice=gemini`); socket.binaryType = "blob"; socket.onopen = () => { if (attempt !== connectionAttemptRef.current) { socket.close(); return; } socketRef.current = socket; setConnected(true); setStatus(conversation.mode === "ielts" ? "Examiner introduction" : "Connecting audio..."); }; socket.onmessage = handleMessage; socket.onerror = () => { console.error("[conversation] WebSocket error"); setStatus("Connection error"); }; socket.onclose = () => { setConnected(false); stopAssistantAudio(); }; } catch { if (attempt === connectionAttemptRef.current) setStatus("Microphone access is required"); } }
  async function ensureAudioContext() { const context = audioContextRef.current ?? new AudioContext({ sampleRate: 16000 }); audioContextRef.current = context; if (context.state === "suspended") await context.resume(); console.info("[conversation] AudioContext ready", context.state); return context; }
  function stopAssistantAudio() { audioGenerationRef.current += 1; audioQueueRef.current = []; audioPlayingRef.current = false; pcmStreamPlayerRef.current?.stop(); pcmStreamPlayerRef.current = null; streamingPcmRef.current = false; pendingPcmSequenceRef.current = null; activePcmStreamIdRef.current = null; for (const pcmSource of pcmSourcesRef.current) { try { pcmSource.stop(); } catch { /* source may already have ended */ } pcmSource.disconnect(); } pcmSourcesRef.current.clear(); if ("speechSynthesis" in window) window.speechSynthesis.cancel(); browserSpeechRef.current = false; const source = audioSourceRef.current; audioSourceRef.current = null; if (source) { try { source.stop(); } catch { /* source may already have ended */ } source.disconnect(); } console.info("[conversation] assistant audio stopped"); }
  function cleanup() { recorderRef.current?.stop(); recorderRef.current = null; captureNodeRef.current?.disconnect(); captureSourceRef.current?.disconnect(); captureNodeRef.current = null; captureSourceRef.current = null; streamRef.current?.getTracks().forEach((track) => track.stop()); stopAssistantAudio(); const audioContext = audioContextRef.current; audioContextRef.current = null; if (audioContext && audioContext.state !== "closed") void audioContext.close().catch(() => undefined); socketRef.current?.close(); }
  function send(event: Record<string, string>) { if (socketRef.current?.readyState === WebSocket.OPEN) socketRef.current.send(JSON.stringify(event)); }
  function startLiveCapture(stream: MediaStream) { void ensureAudioContext().then((context) => { liveActivityRef.current = true; send({ type: "audio_start", contentType: "audio/pcm;rate=16000" }); const source = context.createMediaStreamSource(stream); const processor = context.createScriptProcessor(2048, 1, 1); processor.onaudioprocess = (event) => { if (!recordingRef.current || socketRef.current?.readyState !== WebSocket.OPEN) return; const samples = event.inputBuffer.getChannelData(0); const ratio = context.sampleRate / 16000; const targetLength = Math.floor(samples.length / ratio); const pcm = new Int16Array(targetLength); for (let index = 0; index < targetLength; index += 1) { const sample = samples[Math.min(samples.length - 1, Math.floor(index * ratio))]; pcm[index] = Math.max(-1, Math.min(1, sample)) * 0x7fff; } socketRef.current.send(pcm.buffer); }; source.connect(processor); processor.connect(context.destination); captureSourceRef.current = source; captureNodeRef.current = processor; }); }
  function beginRecording() { const stream = streamRef.current; if (!stream || recordingRef.current || socketRef.current?.readyState !== WebSocket.OPEN) return; recordingRef.current = true; if (liveTransportRef.current) { setRecording(true); startLiveCapture(stream); setStatus("Listening... Press again when you are finished."); return; } void ensureAudioContext().catch((error) => console.error("[conversation] AudioContext resume failed", error)); const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "audio/webm"; const recorder = new MediaRecorder(stream, { mimeType: mime }); chunksRef.current = []; recorder.ondataavailable = (event) => { if (event.data.size) chunksRef.current.push(event.data); }; recorder.onstop = async () => { const audio = new Blob(chunksRef.current, { type: mime }); send({ type: "audio_start", contentType: mime }); if (audio.size && socketRef.current?.readyState === WebSocket.OPEN) { socketRef.current.send(await audio.arrayBuffer()); send({ type: "audio_end" }); setStatus("Thinking..."); } }; recorderRef.current = recorder; recorder.start(250); setRecording(true); setStatus("Listening... Press again when you are finished."); }
  function finishRecording() { recordingRef.current = false; clientTurnStartRef.current = performance.now(); currentTurnRef.current = { start: clientTurnStartRef.current, chunkIntervals: [] }; previousClientAudioAtRef.current = null; playbackStartedRef.current = false; firstAudioReceivedRef.current = null; turnTextAtRef.current = null; if (liveTransportRef.current) { captureNodeRef.current?.disconnect(); captureSourceRef.current?.disconnect(); captureNodeRef.current = null; captureSourceRef.current = null; if (liveActivityRef.current) send({ type: "audio_end" }); liveActivityRef.current = false; setStatus("Thinking..."); } if (recorderRef.current?.state === "recording") recorderRef.current.stop(); recorderRef.current = null; setRecording(false); }
  async function playPcmChunk(blob: Blob) { const generation = audioGenerationRef.current; const context = await ensureAudioContext(); const data = new DataView(await blob.arrayBuffer()); const frames = Math.floor(data.byteLength / 2); const buffer = context.createBuffer(1, frames, 24000); const channel = buffer.getChannelData(0); for (let index = 0; index < frames; index += 1) channel[index] = data.getInt16(index * 2, true) / 0x8000; const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination); const startAt = Math.max(context.currentTime + 0.03, nextPcmStartRef.current); nextPcmStartRef.current = startAt + buffer.duration; audioPlayingRef.current = true; pcmSourcesRef.current.add(source); if (clientTurnStartRef.current !== null && !playbackStartedRef.current) recordPlaybackStart(); source.onended = () => { pcmSourcesRef.current.delete(source); if (generation !== audioGenerationRef.current) return; if (pcmSourcesRef.current.size === 0 && liveAudioEndedRef.current) { audioPlayingRef.current = false; recordPlaybackComplete(); setStatus("Press Speak when you are ready."); } }; source.start(startAt); }
  function recordPlaybackStart(playbackPerformanceTime = performance.now()) { const turn = currentTurnRef.current; if (!turn || playbackStartedRef.current) return; playbackStartedRef.current = true; turn.playbackStart = playbackPerformanceTime - turn.start; const playbackStart = turn.playbackStart; const firstAudio = firstAudioReceivedRef.current === null ? undefined : firstAudioReceivedRef.current - turn.start; const ttft = turn.firstText === undefined ? undefined : turn.firstText - turn.start; setLatestLatency({ ttft, firstAudio, playbackStart }); }
  function recordPlaybackComplete(playbackPerformanceTime = performance.now()) {
    const turn = currentTurnRef.current;
    if (!turn) return;
    turn.playbackComplete = playbackPerformanceTime - turn.start;
    const playbackComplete = turn.playbackComplete;
    setLatestLatency((prior) => ({ ...prior, playbackComplete }));
    setLatencies((all) => [...all, playbackComplete].slice(-100));
    commitBenchmarkTurn();
  }

  function commitBenchmarkTurn() {
    const turn = currentTurnRef.current;
    if (!turn || turn.playbackComplete === undefined || !turn.backend) return;
    const captured: CapturedLatencyTurn = {
      end_of_speech_to_first_text_ms: turn.firstText === undefined ? null : Math.round(turn.firstText - turn.start),
      backend_end_of_speech_to_first_gemini_audio_ms: typeof turn.backend.endOfSpeechToFirstAudioMs === "number" ? turn.backend.endOfSpeechToFirstAudioMs as number : null,
      backend_end_of_speech_to_first_audio_sent_ms: typeof turn.backend.endOfSpeechToFirstAudioSentMs === "number" ? turn.backend.endOfSpeechToFirstAudioSentMs as number : null,
      frontend_end_of_speech_to_first_audio_ms: firstAudioReceivedRef.current === null ? null : Math.round(firstAudioReceivedRef.current - turn.start),
      frontend_end_of_speech_to_playback_start_ms: turn.playbackStart === undefined ? null : Math.round(turn.playbackStart),
      backend_audio_generation_complete_ms: typeof turn.backend.audioGenerationCompleteMs === "number" ? turn.backend.audioGenerationCompleteMs as number : null,
      backend_first_gemini_audio_to_generation_complete_ms: typeof turn.backend.firstGeminiAudioToGenerationCompleteMs === "number" ? turn.backend.firstGeminiAudioToGenerationCompleteMs as number : null,
      backend_gemini_first_chunk_to_first_send_ms: typeof turn.backend.geminiFirstChunkToBackendFirstSendMs === "number" ? turn.backend.geminiFirstChunkToBackendFirstSendMs as number : null,
      backend_last_audio_send_to_generation_complete_ms: typeof turn.backend.lastAudioSendToGenerationCompleteMs === "number" ? turn.backend.lastAudioSendToGenerationCompleteMs as number : null,
      frontend_audio_chunk_intervals_ms: turn.chunkIntervals.map((value) => Math.round(value * 10) / 10),
      backend_gemini_audio_chunk_intervals_ms: Array.isArray(turn.backend.geminiAudioChunkIntervalsMs) ? turn.backend.geminiAudioChunkIntervalsMs as number[] : [],
      frontend_playback_complete_ms: Math.round(turn.playbackComplete),
      backend_audio_chunk_intervals_ms: Array.isArray(turn.backend.backendChunkIntervalsMs) ? turn.backend.backendChunkIntervalsMs as number[] : [],
    };
    setBenchmarkTurns((all) => [...all, captured]);
    currentTurnRef.current = null;
    clientTurnStartRef.current = null;
  }

  async function playNextAudio(generation: number) { if (audioPlayingRef.current || audioQueueRef.current.length === 0 || generation !== audioGenerationRef.current) return; const blob = audioQueueRef.current.shift(); if (!blob) return; audioPlayingRef.current = true; try { const context = await ensureAudioContext(); if (generation !== audioGenerationRef.current) { audioPlayingRef.current = false; return; } const buffer = await context.decodeAudioData(await blob.arrayBuffer()); if (generation !== audioGenerationRef.current) { audioPlayingRef.current = false; return; } const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination); audioSourceRef.current = source; console.info("[conversation] Playing assistant audio", { bytes: blob.size, duration: buffer.duration }); recordPlaybackStart(); source.onended = () => { if (audioSourceRef.current === source) audioSourceRef.current = null; audioPlayingRef.current = false; if (generation !== audioGenerationRef.current) return; if (audioQueueRef.current.length) void playNextAudio(generation); else { recordPlaybackComplete(); setStatus("Press Speak when you are ready."); } }; source.start(); } catch (error) { audioPlayingRef.current = false; console.error("[conversation] Audio playback failed", error); setStatus("Audio playback failed"); void playNextAudio(generation); } }
  function speakImmediately(text: string) { if (!("speechSynthesis" in window)) { setStatus("Browser speech is unavailable"); return; } window.speechSynthesis.cancel(); const utterance = new SpeechSynthesisUtterance(text); utterance.rate = 0.98; utterance.onstart = () => { browserSpeechRef.current = true; console.info("[conversation] Browser speech started"); }; utterance.onend = () => { browserSpeechRef.current = false; setStatus("Press Speak when you are ready."); }; utterance.onerror = (error) => { browserSpeechRef.current = false; console.error("[conversation] Browser speech failed", error); }; window.speechSynthesis.speak(utterance); }
  function handleMessage(event: MessageEvent) {
    if (typeof event.data !== "string") {
      const chunk = event.data instanceof Blob ? event.data : new Blob([event.data]);
      const receivedAt = performance.now();
      const turn = currentTurnRef.current;
      if (turn && firstAudioReceivedRef.current === null) firstAudioReceivedRef.current = receivedAt;
      if (turn && previousClientAudioAtRef.current !== null) turn.chunkIntervals.push(receivedAt - previousClientAudioAtRef.current);
      previousClientAudioAtRef.current = receivedAt;
      if (streamingPcmRef.current) {
        const sequence = pendingPcmSequenceRef.current;
        pendingPcmSequenceRef.current = null;
        if (sequence === null || !pcmStreamPlayerRef.current) {
          console.error("[conversation] PCM data arrived without an audio_chunk event");
          return;
        }
        void pcmStreamPlayerRef.current.append(sequence, chunk).catch((error) => {
          console.error("[conversation] Invalid or out-of-order PCM stream", error);
          stopAssistantAudio();
          setStatus("Live audio stream was interrupted");
        });
        return;
      }
      if (responseMimeRef.current.startsWith("audio/pcm")) { void playPcmChunk(chunk); return; }
      responseChunksRef.current.push(chunk);
      console.info("[conversation] Received audio chunk", chunk.size);
      return;
    }
    const message = JSON.parse(event.data) as { type: string; text?: string; mimeType?: string; message?: string; state?: string; deadlineAt?: number | null; remainingSeconds?: number | null; mode?: string; transport?: string; provider?: string; model?: string; streamingMode?: string; latencyConfig?: Record<string, unknown> | null; streaming?: boolean; streamId?: number; sequence?: number; sampleRateHz?: number; channels?: number; pcmFormat?: string; interrupted?: boolean; [key: string]: unknown };
    console.info("[conversation] Received event", message.type);
    if (message.type === "latency_metrics") {
      setLatestBackendLatency(message);
      if (currentTurnRef.current) { currentTurnRef.current.backend = message; commitBenchmarkTurn(); }
      console.info("[conversation] Backend latency metrics", message);
    }
    if (message.type === "session_ready") {
      liveTransportRef.current = message.transport === "live_pcm";
      benchmarkMetadataRef.current = { provider: message.provider, model: message.model, streamingMode: message.streamingMode, latencyConfig: message.latencyConfig, voiceMode: message.voiceMode, transport: message.transport, conversationMode: conversation.mode };
      setStatus("Press Speak when you are ready.");
    }
    if (message.type === "transcript" && message.text) setLines((items) => [...items, { role: "user", text: message.text! }]);
    if (message.type === "assistant_delta" && turnTextAtRef.current === null) {
      turnTextAtRef.current = performance.now();
      if (currentTurnRef.current) currentTurnRef.current.firstText = turnTextAtRef.current;
    }
    if (message.type === "assistant_text" && message.text) {
      if (turnTextAtRef.current === null) {
        turnTextAtRef.current = performance.now();
        if (currentTurnRef.current) currentTurnRef.current.firstText = turnTextAtRef.current;
      }
      lastAssistantTextRef.current = message.text;
      setLines((items) => [...items, { role: "assistant", text: message.text! }]);
    }
    if (message.type === "exam_state" && message.state) { setExamState(message.state); setExamDeadline(message.deadlineAt ?? null); setExamRemaining(message.remainingSeconds ?? null); setStatus(message.state === "PREPARATION" ? "Preparation time" : message.state === "LONG_TURN" ? "Your long turn" : "Examiner listening"); }
    if (message.type === "processing") setStatus("Thinking...");
    if (message.type === "voice_mode") browserSpeechRef.current = message.mode === "browser";
    if (message.type === "audio_start") {
      responseChunksRef.current = [];
      responseMimeRef.current = message.mimeType ?? "audio/wav";
      liveAudioEndedRef.current = false;
      if (message.streaming && responseMimeRef.current.startsWith("audio/pcm")) {
        if (message.sampleRateHz !== 24000 || message.channels !== 1 || message.pcmFormat !== "s16le") {
          console.error("[conversation] Unsupported Live PCM format", message);
          return;
        }
        const context = audioContextRef.current;
        if (!context) { setStatus("Audio playback is unavailable"); return; }
        pcmStreamPlayerRef.current?.stop();
        pcmStreamPlayerRef.current = new PcmStreamPlayer(context, {
          onPlaybackStart: (time) => recordPlaybackStart(time),
          onPlaybackComplete: (time) => { recordPlaybackComplete(time); setStatus("Press Speak when you are ready."); },
        }, message.sampleRateHz);
        pcmStreamPlayerRef.current.start();
        streamingPcmRef.current = true;
        nextClientPcmSequenceRef.current = 0;
        pendingPcmSequenceRef.current = null;
        activePcmStreamIdRef.current = typeof message.streamId === "number" ? message.streamId : null;
      }
      if (responseMimeRef.current.startsWith("audio/pcm") && pcmSourcesRef.current.size === 0) nextPcmStartRef.current = 0;
      console.info("[conversation] Audio stream started", responseMimeRef.current);
    }
    if (message.type === "audio_chunk" && streamingPcmRef.current) {
      if (message.streamId !== activePcmStreamIdRef.current || message.sequence !== nextClientPcmSequenceRef.current || pendingPcmSequenceRef.current !== null) {
        console.error("[conversation] Invalid PCM chunk sequence", message.sequence);
        stopAssistantAudio();
        return;
      }
      pendingPcmSequenceRef.current = message.sequence;
      nextClientPcmSequenceRef.current += 1;
    }
    if (message.type === "audio_end" && !browserSpeechRef.current) {
      if (streamingPcmRef.current) {
        if (message.streamId !== activePcmStreamIdRef.current) { console.error("[conversation] PCM end event stream ID mismatch"); stopAssistantAudio(); return; }
        if (message.interrupted) { stopAssistantAudio(); currentTurnRef.current = null; clientTurnStartRef.current = null; }
        else if (pendingPcmSequenceRef.current !== null) {
          console.error("[conversation] PCM stream ended before its chunk data frame");
          stopAssistantAudio();
        } else {
          pcmStreamPlayerRef.current?.finish();
          liveAudioEndedRef.current = true;
          streamingPcmRef.current = false;
        }
      } else if (responseMimeRef.current.startsWith("audio/pcm")) {
        liveAudioEndedRef.current = true;
        if (pcmSourcesRef.current.size === 0) setStatus("Press Speak when you are ready.");
      } else {
        const generation = audioGenerationRef.current;
        const audio = new Blob(responseChunksRef.current, { type: responseMimeRef.current });
        console.info("[conversation] Audio stream complete", { bytes: audio.size, contentType: responseMimeRef.current });
        if (audio.size) { audioQueueRef.current.push(audio); void playNextAudio(generation); }
      }
    }
    if (message.type === "response_interrupted") {
      stopAssistantAudio();
      currentTurnRef.current = null;
      clientTurnStartRef.current = null;
    }
    if (message.type === "ready") setStatus("Press Speak when you are ready.");
    if (message.type === "conversation_ending") setStatus("Wrapping up...");
    if (message.type === "exam_finished") setStatus("Exam completed. Your evaluation is being prepared...");
    if (message.type === "voice_error") setStatus(message.message ?? "Voice playback unavailable");
    if (message.type === "exam_wait") setStatus(message.message ?? "Please wait for the examiner");
    if (message.type === "error") setStatus(message.message ?? "Something went wrong");
  }
  function downloadLatencyBenchmark() {
    const timestamp = new Date().toISOString();
    const exportData = { schema_version: 1, generated_at: timestamp, metadata: benchmarkMetadataRef.current, completed_turns: benchmarkTurns };
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `latency-export-${timestamp.replace(/[:.]/g, "-")}.json`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function end() { send({ type: "close" }); cleanup(); void api<Conversation>(`/api/conversations/${conversation.id}/end`, { method: "POST", body: JSON.stringify({ duration_seconds: seconds }) }).then(onDone).catch(() => onDone(conversation)); }
  const orderedLatencies = [...latencies].sort((a, b) => a - b); const percentile = (p: number) => orderedLatencies.length ? orderedLatencies[Math.ceil(p * orderedLatencies.length) - 1] : 0; return <div className="page conversation-page"><div className="conversation-header"><div><p className="eyebrow">{conversation.mode === "ielts" ? `IELTS Speaking · ${examState}` : conversation.topic}</p><h1>{conversation.mode === "ielts" ? "The speaking test." : "Stay curious."}</h1></div><button className="quiet-button" onClick={end}>End session</button></div><div className="session-meta"><span className={connected ? "live-dot" : ""}>● {status}</span><span>{examRemaining !== null ? `${examRemaining}s` : `${Math.floor(seconds / 60).toString().padStart(2, "0")} : ${(seconds % 60).toString().padStart(2, "0")}`}</span></div>{latestLatency && <details className="latency-panel"><summary>Voice latency - {latencies.length} completed turns</summary><p>TTFT {latestLatency.ttft?.toFixed(0) ?? "-"} ms - first audio {latestLatency.firstAudio?.toFixed(0) ?? "-"} ms - playback start {latestLatency.playbackStart?.toFixed(0) ?? "-"} ms - complete {latestLatency.playbackComplete?.toFixed(0) ?? "-"} ms</p>{latestBackendLatency && <p>Backend first Gemini chunk {typeof latestBackendLatency.endOfSpeechToFirstAudioMs === "number" ? latestBackendLatency.endOfSpeechToFirstAudioMs.toFixed(0) : "-"} ms - first chunk sent {typeof latestBackendLatency.endOfSpeechToFirstAudioSentMs === "number" ? latestBackendLatency.endOfSpeechToFirstAudioSentMs.toFixed(0) : "-"} ms - generation complete {typeof latestBackendLatency.audioGenerationCompleteMs === "number" ? latestBackendLatency.audioGenerationCompleteMs.toFixed(0) : "-"} ms</p>}{latencies.length > 0 && <small>Complete playback avg { (latencies.reduce((a, b) => a + b, 0) / latencies.length).toFixed(0) } ms - min {Math.min(...latencies).toFixed(0)} ms - max {Math.max(...latencies).toFixed(0)} ms - P50 {percentile(0.5).toFixed(0)} ms - P95 {percentile(0.95).toFixed(0)} ms</small>}<button className="quiet-button" disabled={!benchmarkTurns.length} onClick={downloadLatencyBenchmark}>Download latency benchmark JSON</button></details>}<div className="transcript-panel">{lines.length === 0 ? <div className="empty-transcript"><span>◌</span><p>Take your time.<br />Your conversation will unfold here.</p></div> : lines.map((line, index) => <div className={`transcript-line ${line.role}`} key={`${line.role}-${index}`}><small>{line.role === "user" ? "YOU" : "EXAMINER"}</small><p>{line.text}</p></div>)}</div><div className="conversation-controls"><button className={recording ? "record-button active" : "record-button"} onClick={recording ? finishRecording : beginRecording}>{recording ? "Finish & respond" : "Speak"}</button><span>{connected ? "Press Speak to start, then Finish & respond when done" : "Connecting..."}</span></div></div>;
}

function EvaluationView({ evaluationId, practiceAttemptId, onStartPractice }: { evaluationId: string; practiceAttemptId: string | null; onStartPractice: (attempt: PracticeAttempt) => void }) {
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [attempt, setAttempt] = useState<PracticeAttempt | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<Evaluation>(`/api/conversations/${evaluationId}/evaluation`).then(setEvaluation).catch((reason) => setError(reason instanceof Error ? reason.message : "Evaluation is not available")); }, [evaluationId]);
  useEffect(() => { if (practiceAttemptId) api<PracticeAttempt>(`/api/practice/${practiceAttemptId}`).then(setAttempt).catch(() => undefined); }, [practiceAttemptId]);
  async function startExercise(exercise: Exercise) { try { const created = await api<PracticeAttempt>(`/api/evaluations/${evaluationId}/practice`, { method: "POST", body: JSON.stringify(exercise) }); onStartPractice(created); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not start practice"); } }
  if (!evaluation) return <main className="page"><p className="eyebrow">Evaluation</p><h1>Preparing your report.</h1>{error && <p className="form-error">{error}</p>}</main>;
  return <div className="page evaluation-page"><p className="eyebrow">AI estimate, not official IELTS scoring</p><h1>Your speaking report.</h1><p className="evaluation-notice">{evaluation.feedback.estimate_notice}</p><div className="score-grid">{["overall", "fluency", "vocabulary", "grammar", "pronunciation"].map((key) => <div className="score-card" key={key}><span>{key}</span><strong>{evaluation.scores[key]?.toFixed(1) ?? "-"}</strong></div>)}</div><div className="evaluation-columns"><section><h2>What is working</h2><ul>{evaluation.feedback.strengths.map((item) => <li key={item}>{item}</li>)}</ul><h2>Focus next</h2><ul>{evaluation.feedback.weaknesses.map((item) => <li key={item}>{item}</li>)}</ul><h2>Recommendations</h2><ul>{evaluation.recommendations.map((item) => <li key={item}>{item}</li>)}</ul></section><section><h2>Coach exercises</h2>{evaluation.coach_exercises.map((exercise) => <div className="exercise-card" key={`${exercise.skill}-${exercise.prompt}`}><span>{exercise.skill}</span><strong>{exercise.prompt}</strong><small>{exercise.target_duration_seconds}s target</small><button className="primary-button" onClick={() => startExercise(exercise)}>Start practice</button></div>)}</section></div>{attempt && <section className="comparison-panel"><h2>Practice comparison</h2><p>{attempt.comparison.improvements?.join(" ") || "Your first attempt is now recorded."}</p><p>Remaining focus: {attempt.comparison.remaining_weaknesses?.join(", ") || "Keep building consistency."}</p></section>}</div>;
}

function ProgressView() {
  const [progress, setProgress] = useState<Progress | null>(null);
  useEffect(() => { api<Progress>("/api/progress").then(setProgress).catch(() => setProgress(null)); }, []);
  if (!progress) return <main className="page"><p className="eyebrow">Progress</p><h1>Building your path.</h1></main>;
  return <div className="page"><p className="eyebrow">Your long-term practice</p><h1>Progress.</h1><section className="recommendation-panel"><span>RECOMMENDED NEXT</span><strong>{progress.recommended_next.prompt}</strong><p>{progress.recommended_next.skill} · {progress.recommended_next.target_duration_seconds}s</p></section><div className="score-grid">{Object.entries(progress.trends).map(([skill, trend]) => <div className="score-card" key={skill}><span>{skill}</span><strong>{trend.latest?.toFixed(1) ?? "-"}</strong><small>{trend.change > 0 ? `+${trend.change.toFixed(1)}` : trend.change.toFixed(1)} trend</small></div>)}</div><div className="evaluation-columns"><section><h2>Recurring weaknesses</h2><ul>{progress.recurring_weaknesses.length ? progress.recurring_weaknesses.map((skill) => <li key={skill}>{skill}</li>) : <li>No recurring weakness yet.</li>}</ul></section><section><h2>Score history</h2><ul>{progress.score_history.slice(-6).map((entry) => <li key={`${entry.created_at}-${entry.kind}`}>{entry.kind} · {new Date(entry.created_at).toLocaleDateString()} · overall {entry.scores.overall?.toFixed(1) ?? "-"}</li>)}</ul><p className="cost-note">Tracked AI usage: ${progress.cost.total_usd.toFixed(4)} across {progress.cost.records} records.</p></section></div></div>;
}

function HistoryView({ onSelect }: { onSelect: (conversation: Conversation) => void }) {
  const [items, setItems] = useState<Conversation[]>([]);
  useEffect(() => { api<Conversation[]>("/api/conversations").then(setItems).catch(() => setItems([])); }, []);
  async function remove(item: Conversation) {
    if (!window.confirm(`Delete “${item.topic}”? This cannot be undone.`)) return;
    await api<void>(`/api/conversations/${item.id}`, { method: "DELETE" });
    setItems((current) => current.filter((candidate) => candidate.id !== item.id));
  }
  return <div className="page"><div className="page-heading"><div><p className="eyebrow">Your archive</p><h1>Conversations.</h1></div><span>{items.length} sessions</span></div>{items.length === 0 ? <div className="empty-state-large"><strong>No conversations yet.</strong><p>Your first thoughtful exchange will live here.</p></div> : <div className="history-list">{items.map((item) => <div className="history-item" key={item.id}><button onClick={() => onSelect(item)}><span className="history-date">{new Date(item.started_at).toLocaleDateString()}</span><strong>{item.topic}</strong><span>{item.mode === "ielts" ? "IELTS" : item.mode === "practice" ? "Practice" : item.duration_seconds ? `${Math.ceil(item.duration_seconds / 60)} min` : "In progress"}</span><span>→</span></button><button className="history-delete" aria-label={`Delete ${item.topic}`} onClick={() => void remove(item)}>Delete</button></div>)}</div>}</div>;
}

function ProfileView({ user, onSaved }: { user: User; onSaved: (user: User) => void }) { const [name, setName] = useState(user.display_name); const [saved, setSaved] = useState(false); async function save() { const updated = await api<User>("/api/auth/me", { method: "PATCH", body: JSON.stringify({ ...user.preferences, display_name: name }) }); onSaved(updated); setSaved(true); } return <div className="page narrow-page"><p className="eyebrow">Your account</p><h1>Profile.</h1><div className="form-section"><label>Display name<input value={name} onChange={(event) => { setName(event.target.value); setSaved(false); }} /></label><label>Email<input value={user.email} disabled /></label><button className="primary-button" onClick={save}>{saved ? "Saved" : "Save profile"}</button></div></div>; }

function SettingsView({ user, onSaved }: { user: User; onSaved: (user: User) => void }) { const [preferences, setPreferences] = useState(user.preferences); async function save() { const updated = await api<User>("/api/auth/me", { method: "PATCH", body: JSON.stringify(preferences) }); onSaved(updated); } return <div className="page narrow-page"><p className="eyebrow">Make it yours</p><h1>Settings.</h1><div className="form-section"><label>Voice<select value={preferences.voice} onChange={(event) => setPreferences({ ...preferences, voice: event.target.value })}><option>Kore</option><option>Puck</option><option>Aoede</option><option>Charon</option></select></label><label>Conversation style<select value={preferences.speaking_style} onChange={(event) => setPreferences({ ...preferences, speaking_style: event.target.value })}><option value="natural">Natural and easy</option><option value="friendly">Friendly and energetic</option><option value="calm">Calm and thoughtful</option></select></label><label className="checkbox"><input type="checkbox" checked={preferences.auto_start} onChange={(event) => setPreferences({ ...preferences, auto_start: event.target.checked })} /> Start listening automatically</label><button className="primary-button" onClick={save}>Save settings</button></div></div>; }
