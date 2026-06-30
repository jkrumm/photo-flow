let ctx: AudioContext | null = null

function getCtx(): AudioContext {
  if (!ctx) ctx = new AudioContext()
  return ctx
}

/** Call inside a user-gesture handler to unblock the AudioContext (browser autoplay policy). */
export function resumeAudio(): void {
  void getCtx().resume()
}

/** Pleasant two-note ascending chime for job success. */
export function playSuccess(): void {
  const ac = getCtx()
  if (ac.state === 'suspended') return
  const t = ac.currentTime
  const osc = ac.createOscillator()
  const gain = ac.createGain()
  osc.connect(gain)
  gain.connect(ac.destination)
  osc.type = 'sine'
  osc.frequency.setValueAtTime(523.25, t)         // C5
  osc.frequency.setValueAtTime(659.25, t + 0.12)  // E5
  gain.gain.setValueAtTime(0.18, t)
  gain.gain.exponentialRampToValueAtTime(0.001, t + 0.5)
  osc.start(t)
  osc.stop(t + 0.5)
}

/** Lower two-note descending tone for job error. */
export function playError(): void {
  const ac = getCtx()
  if (ac.state === 'suspended') return
  const t = ac.currentTime
  const osc = ac.createOscillator()
  const gain = ac.createGain()
  osc.connect(gain)
  gain.connect(ac.destination)
  osc.type = 'sine'
  osc.frequency.setValueAtTime(392, t)         // G4
  osc.frequency.setValueAtTime(349.23, t + 0.18) // F4
  gain.gain.setValueAtTime(0.18, t)
  gain.gain.exponentialRampToValueAtTime(0.001, t + 0.55)
  osc.start(t)
  osc.stop(t + 0.55)
}
