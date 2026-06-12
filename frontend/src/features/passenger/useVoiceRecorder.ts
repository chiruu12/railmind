import { useCallback, useEffect, useRef, useState } from 'react'

const MAX_RECORDING_MS = 30_000

export interface VoiceRecorder {
  recording: boolean
  micError: string | null
  toggle: () => void
}

export function useVoiceRecorder(onAudio: (blob: Blob) => void): VoiceRecorder {
  const [recording, setRecording] = useState(false)
  const [micError, setMicError] = useState<string | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onAudioRef = useRef(onAudio)
  useEffect(() => { onAudioRef.current = onAudio })

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop()
    }
    recorderRef.current = null
    setRecording(false)
  }, [])

  const start = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mimeType = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : undefined
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      chunksRef.current = []
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(chunksRef.current, { type: mimeType ?? 'audio/webm' })
        if (blob.size > 0) onAudioRef.current(blob)
      }
      recorder.start()
      recorderRef.current = recorder
      setMicError(null)
      setRecording(true)
      timerRef.current = setTimeout(stop, MAX_RECORDING_MS)
    } catch {
      setMicError('Microphone unavailable — type your question instead.')
      setRecording(false)
    }
  }, [stop])

  const toggle = useCallback(() => {
    if (recording) {
      stop()
    } else {
      void start()
    }
  }, [recording, start, stop])

  return { recording, micError, toggle }
}
